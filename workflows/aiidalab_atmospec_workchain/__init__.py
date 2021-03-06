"""Base work chain to run an ORCA calculation"""

import ase
from aiida.engine import WorkChain, calcfunction
from aiida.engine import append_, ToContext, if_

# not sure if this is needed? Can we use self.run()?
from aiida.engine import run
from aiida.plugins import CalculationFactory, WorkflowFactory, DataFactory
from aiida.orm import to_aiida_type

from .wigner import Wigner

StructureData = DataFactory("structure")
TrajectoryData = DataFactory("array.trajectory")
Int = DataFactory("int")
Bool = DataFactory("bool")
Code = DataFactory("code")
List = DataFactory("list")
Dict = DataFactory("dict")

OrcaCalculation = CalculationFactory("orca_main")
OrcaBaseWorkChain = WorkflowFactory("orca.base")


# Meta WorkChain for combining all inputs from a dynamic namespace into List.
# Used to combine outputs from several subworkflows into one output.
# It should be launched via run() instead of submit()
# NOTE: The code has special handling for Dict nodes,
# which otherwise fail with not being serializable,
# so we need the get the value with Dict.get_dict() first.
# We should check whether this is still needed in aiida-2.0
# Note we cannot make this more general since List and Dict
# don't have the .value attribute.
# https://github.com/aiidateam/aiida-core/issues/5313
class ConcatInputsToList(WorkChain):
    @classmethod
    def define(cls, spec):
        super().define(spec)
        spec.input_namespace("ns", dynamic=True)
        spec.output("output", valid_type=List)
        spec.outline(cls.combine)

    def combine(self):
        input_list = [
            self.inputs.ns[k].get_dict()
            if isinstance(self.inputs.ns[k], Dict)
            else self.inputs.ns[k]
            for k in self.inputs.ns
        ]
        self.out("output", List(list=input_list).store())


# TODO: Allow optional inputs for array data to store energies
class ConcatStructuresToTrajectory(WorkChain):
    """WorkChain for combining a list of StructureData into TrajectoryData"""

    @classmethod
    def define(cls, spec):
        super().define(spec)
        # TODO: Maybe allow other types other than StructureData?
        # Not sure what are the requirements for TrajectoryData
        spec.input_namespace("structures", dynamic=True, valid_type=StructureData)
        spec.output("trajectory", valid_type=TrajectoryData)
        spec.outline(cls.combine)

    def combine(self):
        structurelist = [self.inputs.structures[k] for k in self.inputs.structures]
        self.out("trajectory", TrajectoryData(structurelist=structurelist).store())


@calcfunction
def pick_wigner_structure(wigner_structures, index):
    return wigner_structures.get_step_structure(index.value)


@calcfunction
def generate_wigner_structures(orca_output_dict, nsample):
    seed = orca_output_dict.extras["_aiida_hash"]

    frequencies = orca_output_dict["vibfreqs"]
    masses = orca_output_dict["atommasses"]
    normal_modes = orca_output_dict["vibdisps"]
    elements = orca_output_dict["elements"]
    min_coord = orca_output_dict["atomcoords"][-1]
    natom = orca_output_dict["natom"]
    # convert to Bohrs
    ANG2BOHRS = 1.0 / 0.529177211
    coordinates = []
    # TODO: Do the conversion in wigner.py
    # TODO: Use ASE object in wigner.py
    for iat in range(natom):
        coordinates.append(
            [
                min_coord[iat][0] * ANG2BOHRS,
                min_coord[iat][1] * ANG2BOHRS,
                min_coord[iat][2] * ANG2BOHRS,
            ]
        )

    w = Wigner(elements, masses, coordinates, frequencies, normal_modes, seed)

    wigner_list = []
    for i in range(nsample.value):
        wigner_coord = w.get_sample()
        # Convert to angstroms
        wigner_coord_ang = []
        for iat in range(natom):
            wigner_coord_ang.append(
                [
                    wigner_coord[iat][0] / ANG2BOHRS,
                    wigner_coord[iat][1] / ANG2BOHRS,
                    wigner_coord[iat][2] / ANG2BOHRS,
                ]
            )
        # TODO: We shouldn't need to specify cell
        # https://github.com/aiidateam/aiida-core/issues/5248
        ase_struct = ase.Atoms(
            positions=wigner_coord_ang,
            symbols=elements,
            cell=(1.0, 1.0, 1.0),
            pbc=False,
        )
        wigner_list.append(StructureData(ase=ase_struct))

    return TrajectoryData(structurelist=wigner_list)


class OrcaWignerSpectrumWorkChain(WorkChain):
    """Basic workchain for single point TDDFT on optimized geometry"""

    @classmethod
    def define(cls, spec):
        super().define(spec)
        spec.expose_inputs(
            OrcaBaseWorkChain, namespace="opt", exclude=["orca.structure", "orca.code"]
        )
        spec.expose_inputs(
            OrcaBaseWorkChain, namespace="exc", exclude=["orca.structure", "orca.code"]
        )
        spec.input("structure", valid_type=(StructureData, TrajectoryData))
        spec.input("code", valid_type=Code)

        # Whether to perform geometry optimization
        spec.input(
            "optimize",
            valid_type=Bool,
            default=lambda: Bool(True),
            serializer=to_aiida_type,
        )

        # Number of Wigner geometries (computed only when optimize==True)
        spec.input(
            "nwigner", valid_type=Int, default=lambda: Int(1), serializer=to_aiida_type
        )

        spec.output("relaxed_structure", valid_type=StructureData, required=False)
        spec.output(
            "single_point_tddft",
            valid_type=Dict,
            required=True,
            help="Output parameters from a single-point TDDFT calculation",
        )

        # TODO: Rename this port
        spec.output(
            "wigner_tddft",
            valid_type=List,
            required=False,
            help="Output parameters from all Wigner TDDFT calculation",
        )

        spec.outline(
            cls.setup,
            if_(cls.should_optimize)(
                cls.optimize,
                cls.inspect_optimization,
            ),
            cls.excite,
            cls.inspect_excitation,
            if_(cls.should_run_wigner)(
                cls.wigner_sampling,
                cls.wigner_excite,
                cls.inspect_wigner_excitation,
            ),
            cls.results,
        )

        spec.exit_code(
            401,
            "ERROR_OPTIMIZATION_FAILED",
            "optimization encountered unspecified error",
        )
        spec.exit_code(
            402, "ERROR_EXCITATION_FAILED", "excited state calculation failed"
        )

    def setup(self):
        """Setup workchain"""
        # TODO: This should be base on some input parameter
        self.ctx.nstates = 3

    def excite(self):
        """Calculate excited states for a single geometry"""
        inputs = self.exposed_inputs(
            OrcaBaseWorkChain, namespace="exc", agglomerate=False
        )
        inputs.orca.code = self.inputs.code

        if self.inputs.optimize:
            self.report(
                f"Calculating {self.ctx.nstates} excited states for optimized geometry"
            )
            inputs.orca.structure = self.ctx.calc_opt.outputs.relaxed_structure
        else:
            self.report(
                f"Calculating {self.ctx.nstates} excited states for input geometry"
            )
            inputs.orca.structure = self.inputs.structure

        calc_exc = self.submit(OrcaBaseWorkChain, **inputs)
        calc_exc.label = "single-point-tddft"
        return ToContext(calc_exc=calc_exc)

    def wigner_sampling(self):
        self.report(f"Generating {self.inputs.nwigner.value} Wigner geometries")
        self.ctx.wigner_structures = generate_wigner_structures(
            self.ctx.calc_opt.outputs.output_parameters, self.inputs.nwigner
        )

    def wigner_excite(self):
        inputs = self.exposed_inputs(
            OrcaBaseWorkChain, namespace="exc", agglomerate=False
        )
        inputs.orca.code = self.inputs.code
        for i in self.ctx.wigner_structures.get_stepids():
            inputs.orca.structure = pick_wigner_structure(
                self.ctx.wigner_structures, Int(i)
            )
            calc = self.submit(OrcaBaseWorkChain, **inputs)
            calc.label = "wigner-single-point-tddft"
            self.to_context(wigner_calcs=append_(calc))

    def optimize(self):
        """Optimize geometry"""
        inputs = self.exposed_inputs(
            OrcaBaseWorkChain, namespace="opt", agglomerate=False
        )
        inputs.orca.structure = self.inputs.structure
        inputs.orca.code = self.inputs.code

        calc_opt = self.submit(OrcaBaseWorkChain, **inputs)
        return ToContext(calc_opt=calc_opt)

    def inspect_optimization(self):
        """Check whether optimization succeeded"""
        if not self.ctx.calc_opt.is_finished_ok:
            self.report("Optimization failed :-(")
            return self.exit_codes.ERROR_OPTIMIZATION_FAILED

    def inspect_excitation(self):
        """Check whether excitation succeeded"""
        if not self.ctx.calc_exc.is_finished_ok:
            self.report("Single point excitation failed :-(")
            return self.exit_codes.ERROR_EXCITATION_FAILED

    def inspect_wigner_excitation(self):
        """Check whether all wigner excitations succeeded"""
        for calc in self.ctx.wigner_calcs:
            if not calc.is_finished_ok:
                # TODO: Report all failed calcs at once
                self.report("Wigner excitation failed :-(")
                return self.exit_codes.ERROR_EXCITATION_FAILED

    def should_optimize(self):
        if self.inputs.optimize:
            return True
        return False

    def should_run_wigner(self):
        return self.should_optimize() and self.inputs.nwigner > 0

    def results(self):
        """Expose results from child workchains"""

        if self.should_optimize():
            self.out("relaxed_structure", self.ctx.calc_opt.outputs.relaxed_structure)

        if self.should_run_wigner():
            self.report("Concatenating Wigner outputs")
            # TODO: Instead of deepcopying all dicts,
            # only pick the data that we need for the spectrum to save space.
            # We should introduce a special aiida type for spectrum data
            data = {
                str(i): wc.outputs.output_parameters
                for i, wc in enumerate(self.ctx.wigner_calcs)
            }
            all_results = run(ConcatInputsToList, ns=data)
            self.out("wigner_tddft", all_results["output"])

        self.out("single_point_tddft", self.ctx.calc_exc.outputs.output_parameters)


class AtmospecWorkChain(WorkChain):
    """The top-level ATMOSPEC workchain"""

    @classmethod
    def define(cls, spec):
        super().define(spec)
        spec.expose_inputs(OrcaWignerSpectrumWorkChain, exclude=["structure"])
        spec.input("structure", valid_type=(StructureData, TrajectoryData))

        spec.output(
            "spectrum_data",
            valid_type=List,
            required=True,
            help="All data necessary to construct spectrum in SpectrumWidget",
        )

        spec.output(
            "relaxed_structures",
            valid_type=TrajectoryData,
            required=False,
            help="Minimized structures of all conformers",
        )

        spec.outline(
            cls.launch,
            cls.collect,
        )

        # Very generic error now
        spec.exit_code(410, "CONFORMER_ERROR", "Conformer spectrum generation failed")

    def launch(self):
        inputs = self.exposed_inputs(OrcaWignerSpectrumWorkChain, agglomerate=False)
        # Single conformer
        # TODO: Test this!
        if isinstance(self.inputs.structure, StructureData):
            self.report("Launching ATMOSPEC for 1 conformer")
            inputs.structure = self.inputs.structure
            return ToContext(conf=self.submit(OrcaWignerSpectrumWorkChain, **inputs))

        self.report(
            f"Launching ATMOSPEC for {len(self.inputs.structure.get_stepids())} conformers"
        )
        for conf_id in self.inputs.structure.get_stepids():
            inputs.structure = self.inputs.structure.get_step_structure(conf_id)
            workflow = self.submit(OrcaWignerSpectrumWorkChain, **inputs)
            # workflow.label = 'conformer-wigner-spectrum'
            self.to_context(confs=append_(workflow))

    def collect(self):
        # For single conformer
        # TODO: This currently does not work
        if isinstance(self.inputs.structure, StructureData):
            if not self.ctx.conf.is_finished_ok:
                return self.exit_codes.CONFORMER_ERROR
            self.out_many(
                self.exposed_outputs(self.ctx.conf, OrcaWignerSpectrumWorkChain)
            )
            return

        # Check for errors
        # TODO: Raise if subworkflows raised?
        for wc in self.ctx.confs:
            # TODO: Specialize errors. Can we expose errors from child workflows?
            if not wc.is_finished_ok:
                return self.exit_codes.CONFORMER_ERROR

        # Combine all spectra data
        data = {str(i): wc.outputs.wigner_tddft for i, wc in enumerate(self.ctx.confs)}
        all_results = run(ConcatInputsToList, ns=data)
        self.out("spectrum_data", all_results["output"])

        # Combine all optimized geometries into single TrajectoryData
        # TODO: Include energies in TrajectoryData for optimized structures
        if self.inputs.optimize:
            relaxed_structures = {
                str(i): wc.outputs.relaxed_structure
                for i, wc in enumerate(self.ctx.confs)
            }
            output = run(ConcatStructuresToTrajectory, structures=relaxed_structures)
            self.out("relaxed_structures", output["trajectory"])


__version__ = "0.1-alpha"
