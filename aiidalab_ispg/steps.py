"""Widgets for the submission of basic ORCA calculation.
Based on the original code from aiidalab_qe

Authors:
    * Daniel Hollas <daniel.hollas@durham.ac.uk>
    * Carl Simon Adorf <simon.adorf@epfl.ch>
"""
from pprint import pformat

# DH: Hopefully we will be able to remove this
from copy import deepcopy

import ipywidgets as ipw
import traitlets
from traitlets import Union, Instance
from aiida.common import NotExistent
from aiida.engine import ProcessState, submit
from aiida.orm import ProcessNode, load_code

from aiida.orm import WorkChainNode
from aiida.plugins import DataFactory
from aiidalab_widgets_base import (
    CodeDropdown,
    ProcessMonitor,
    ProcessNodesTreeWidget,
    WizardAppWidgetStep,
)

from aiidalab_ispg.parameters import DEFAULT_PARAMETERS
from aiidalab_ispg.widgets import NodeViewWidget, ResourceSelectionWidget
from aiidalab_ispg.widgets import QMSelectionWidget

try:
    from aiidalab_atmospec_workchain import AtmospecWorkChain
except ImportError:
    # TODO: Can we do something better than print here?
    print("ERROR: Could not find aiidalab_atmospec_workchain module!")

from aiidalab_ispg.spectrum import SpectrumWidget

StructureData = DataFactory("structure")
TrajectoryData = DataFactory("array.trajectory")
Dict = DataFactory("dict")


class WorkChainSettings(ipw.VBox):

    structure_title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>Structure</h4></div>"""
    )
    structure_help = ipw.HTML(
        """<div style="line-height: 140%; padding-top: 0px; padding-bottom: 5px">
        By default, the workflow will optimize the provided geometry. Select "Structure
        as is" if this is not desired.</div>"""
    )

    electronic_structure_title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>Electronic structure</h4></div>"""
    )

    button_style_on = "info"
    button_style_off = "danger"

    def __init__(self, **kwargs):

        # Whether to optimize the molecule or not.
        self.geo_opt_type = ipw.ToggleButtons(
            options=[
                ("Structure as is", "NONE"),
                ("Full geometry optimization", "OPT"),
            ],
            value="OPT",
        )

        self.spin_mult = ipw.BoundedIntText(
            min=1,
            max=7,
            step=1,
            description="Spin Multiplicity",
            disabled=False,
            value=1,
        )

        self.charge = ipw.IntText(
            description="Charge",
            disabled=False,
            value=0,
        )

        super().__init__(
            children=[
                self.structure_title,
                self.structure_help,
                self.geo_opt_type,
                self.electronic_structure_title,
                self.charge,
                self.spin_mult,
            ],
            **kwargs,
        )


class CodeSettings(ipw.VBox):

    codes_title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>Codes</h4></div>"""
    )
    codes_help = ipw.HTML(
        """<div style="line-height: 140%; padding-top: 0px; padding-bottom:
        10px"> Select the code to use for running the calculations. The codes
        on the local machine (localhost) are installed by default, but you can
        configure new ones on potentially more powerful machines by clicking on
        "Setup new code".</div>"""
    )

    def __init__(self, **kwargs):

        self.orca = CodeDropdown(
            input_plugin="orca_main",
            description="main orca program",
            setup_code_params={
                "computer": "localhost",
                "description": "orca in AiiDAlab container.",
                "label": "orca",
                "input_plugin": "orca_main",
                "remote_abs_path": "/home/aiida/software/orca/orca_5_0_1_openmpi422/orca",
            },
        )
        super().__init__(
            children=[
                self.codes_title,
                self.codes_help,
                self.orca,
            ],
            **kwargs,
        )


class SubmitAtmospecAppWorkChainStep(ipw.VBox, WizardAppWidgetStep):
    """Step for submission of a bands workchain."""

    input_structure = Union(
        [Instance(StructureData), Instance(TrajectoryData)], allow_none=True
    )
    process = Instance(WorkChainNode, allow_none=True)
    disabled = traitlets.Bool()
    builder_parameters = traitlets.Dict()
    expert_mode = traitlets.Bool()

    def __init__(self, **kwargs):
        self.message_area = ipw.Output()
        self.workchain_settings = WorkChainSettings()
        self.codes_selector = CodeSettings()
        self.resources_config = ResourceSelectionWidget()
        self.qm_config = QMSelectionWidget()

        self.set_trait("builder_parameters", self._default_builder_parameters())
        self._setup_builder_parameters_update()

        self.codes_selector.orca.observe(self._update_state, "selected_code")
        self.codes_selector.orca.observe(
            self._set_num_mpi_tasks_to_default, "selected_code"
        )

        self.tab = ipw.Tab(
            children=[
                self.workchain_settings,
            ],
            layout=ipw.Layout(min_height="250px"),
        )

        self.tab.set_title(0, "Workflow")

        self.submit_button = ipw.Button(
            description="Submit",
            tooltip="Submit the calculation with the selected parameters.",
            icon="play",
            button_style="success",
            layout=ipw.Layout(width="auto", flex="1 1 auto"),
            disabled=True,
        )

        self.submit_button.on_click(self._on_submit_button_clicked)

        self.expert_mode_control = ipw.ToggleButton(
            description="Expert mode",
            tooltip="Activate Expert mode for access to advanced settings.",
            value=True,
        )
        ipw.link((self, "expert_mode"), (self.expert_mode_control, "value"))

        self._update_builder_parameters()

        self.builder_parameters_view = ipw.HTML(layout=ipw.Layout(width="auto"))
        ipw.dlink(
            (self, "builder_parameters"),
            (self.builder_parameters_view, "value"),
            transform=lambda p: '<pre style="line-height: 100%">'
            + pformat(p, indent=2, width=200)
            + "</pre>",
        )

        super().__init__(
            children=[
                self.message_area,
                self.tab,
                ipw.HBox([self.submit_button, self.expert_mode_control]),
            ]
        )

    @traitlets.observe("expert_mode")
    def _observe_expert_mode(self, change):
        if change["new"]:
            self.tab.set_title(0, "Workflow")
            self.tab.set_title(1, "Advanced settings")
            self.tab.set_title(2, "Codes & Resources")
            self.tab.children = [
                self.workchain_settings,
                self.qm_config,
                ipw.VBox(children=[self.codes_selector, self.resources_config]),
            ]
        else:
            self.tab.set_title(0, "Workflow")
            self.tab.children = [
                self.workchain_settings,
            ]

    def _get_state(self):

        # Process is already running.
        if self.process is not None:
            return self.State.SUCCESS

        # Input structure not specified.
        if self.input_structure is None:
            return self.State.INIT

        # ORCA code not selected.
        if self.codes_selector.orca.selected_code is None:
            return self.State.READY

        return self.State.CONFIGURED

    def _update_state(self, _=None):
        self.state = self._get_state()

    _ALERT_MESSAGE = """
        <div class="alert alert-{alert_class} alert-dismissible">
        <a href="#" class="close" data-dismiss="alert" aria-label="close">&times;</a>
        <span class="closebtn" onclick="this.parentElement.style.display='none';">&times;</span>
        <strong>{message}</strong>
        </div>"""

    def _show_alert_message(self, message, alert_class="info"):
        with self.message_area:
            display(  # noqa
                ipw.HTML(
                    self._ALERT_MESSAGE.format(alert_class=alert_class, message=message)
                )
            )

    def _set_num_mpi_tasks_to_default(self, _=None):
        """Set the number of MPI tasks to a reasonable value for the selected structure."""
        # DH: TODO: For now we simply set this to 1
        self.resources_config.num_mpi_tasks.value = 1

    @traitlets.observe("state")
    def _observe_state(self, change):
        with self.hold_trait_notifications():
            self.disabled = change["new"] not in (
                self.State.READY,
                self.State.CONFIGURED,
            )
            self.submit_button.disabled = change["new"] != self.State.CONFIGURED

    @traitlets.observe("input_structure")
    def _observe_input_structure(self, change):
        self.set_trait("builder_parameters", self._default_builder_parameters())
        self._update_state()
        self._set_num_mpi_tasks_to_default()

    @traitlets.observe("process")
    def _observe_process(self, change):
        with self.hold_trait_notifications():
            # process_node = change["new"]
            # DH: Not sure why this is here, but I don't think
            # it quite works for our current setup,
            # so commenting out?
            # if process_node is not None:
            # self.input_structure = process_node.inputs.structure
            # builder_parameters = process_node.get_extra("builder_parameters", None)
            # if builder_parameters is not None:
            #    self.set_trait("builder_parameters", builder_parameters)
            self._update_state()

    def _on_submit_button_clicked(self, _):
        self.submit_button.disabled = True
        self.submit()

    def _setup_builder_parameters_update(self):
        """Set up all ``observe`` calls to monitor changes in user inputs."""
        update = self._update_builder_parameters  # alias for code conciseness
        # Properties
        self.workchain_settings.geo_opt_type.observe(update, ["value"])
        self.workchain_settings.spin_mult.observe(update, ["value"])
        self.workchain_settings.charge.observe(update, ["value"])
        # Codes
        self.codes_selector.orca.observe(update, ["selected_code"])
        # QM settings
        self.qm_config.method.observe(update, ["value"])
        self.qm_config.basis.observe(update, ["value"])

    @staticmethod
    def _serialize_builder_parameters(parameters):
        parameters = parameters.copy()  # create copy to not modify original dict

        # Codes
        def _get_uuid(code):
            return None if code is None else str(code.uuid)

        parameters["orca_code"] = _get_uuid(parameters["orca_code"])
        return parameters

    @staticmethod
    def _deserialize_builder_parameters(parameters):
        parameters = parameters.copy()  # create copy to not modify original dict

        # Codes
        def _load_code(code):
            if code is not None:
                try:
                    return load_code(code)
                except NotExistent as error:
                    print("error", error)
                    return None

        parameters["orca_code"] = _load_code(parameters["orca_code"])
        return parameters

    def _update_builder_parameters(self, _=None):
        self.set_trait(
            "builder_parameters",
            self._serialize_builder_parameters(
                dict(
                    # Codes
                    orca_code=self.codes_selector.orca.selected_code,
                    method=self.qm_config.method.value,
                    basis=self.qm_config.basis.value,
                    charge=self.workchain_settings.charge.value,
                    spin_mult=self.workchain_settings.spin_mult.value,
                )
            ),
        )

    @traitlets.observe("builder_parameters")
    def _observe_builder_parameters(self, change):
        bp = self._deserialize_builder_parameters(change["new"])

        with self.hold_trait_notifications():
            # Workchain settings
            self.workchain_settings.spin_mult.value = bp["spin_mult"]
            self.workchain_settings.charge.value = bp["charge"]
            # Codes
            self.codes_selector.orca.selected_code = bp.get("orca_code")
            # QM settings
            self.qm_config.method.value = bp["method"]
            self.qm_config.basis.value = bp["basis"]

    def build_base_orca_params(self, builder_parameters):
        """A bit of indirection to decouple aiida-orca plugin
        from this code"""

        input_keywords = [builder_parameters[key] for key in ("basis", "method")]

        params = {
            "charge": builder_parameters["charge"],
            "multiplicity": builder_parameters["spin_mult"],
            "input_blocks": {
                "scf": {"convergence": "tight", "ConvForced": "true"},
            },
            "input_keywords": input_keywords,
            "extra_input_keywords": [],
        }

        return params

    def add_tddft_orca_params(self, orca_parameters, nroots):
        parameters = deepcopy(orca_parameters)
        tddft = {
            "nroots": nroots,
        }
        parameters["input_blocks"]["tddft"] = tddft
        return parameters

    def add_compound_optimization(self, orca_parameters, basis, method):
        parameters = deepcopy(orca_parameters)
        # TODO: Make this work in orca plugin
        parameters["input_blocks"]["compound"] = "iterativeOptimization.cmp"
        # TODO:
        # with open("parameters/iterativeOptimization.cmp") as f:
        #    s = f.read().format(basis=basis, method=method)
        # TODO: Store s as "SingleFileData"
        # https://stackoverflow.com/questions/7585435/best-way-to-convert-string-to-bytes-in-python-3
        # file_node = SingleFileData(s)
        # parameters.file['compound'] = file_node
        return parameters

    def submit(self, _=None):

        assert self.input_structure is not None

        builder_parameters = self.builder_parameters.copy()

        builder = AtmospecWorkChain.get_builder()

        orca_code = self.codes_selector.orca.selected_code
        builder.code = orca_code
        builder.structure = self.input_structure

        orca_parameters = self.build_base_orca_params(builder_parameters)
        # TODO: Make this an option in the UI
        # or rather, autodetermine based on requested energy range.
        nroots = 3
        tddft_parameters = self.add_tddft_orca_params(orca_parameters, nroots)
        optimization_parameters = deepcopy(orca_parameters)
        optimization_parameters["input_keywords"].append("TightOpt")
        optimization_parameters["input_keywords"].append("AnFreq")

        builder.exc.orca.parameters = Dict(dict=tddft_parameters)
        builder.opt.orca.parameters = Dict(dict=optimization_parameters)

        num_proc = self.resources_config.num_mpi_tasks.value
        if num_proc > 1:
            print(f"Running on {num_proc} CPUs")
            # Not sure if this works
            orca_parameters["input_blocks"]["pal"] = {"nproc": num_proc}

        metadata = {
            "options": {
                "withmpi": False,
                "resources": {"tot_num_mpiprocs": num_proc},
            }
        }
        builder.exc.orca.metadata = metadata
        builder.opt.orca.metadata = metadata

        builder.exc.orca.metadata.description = "ORCA TDDFT calculation"
        builder.opt.orca.metadata.description = "ORCA geometry optimization"

        if self.workchain_settings.geo_opt_type.value == "NONE":
            builder.optimize = False

        # Wigner will be sampled only when optimize == True
        builder.nwigner = self.qm_config.nwigner.value

        self.process = submit(builder)

        self.process.set_extra("builder_parameters", self.builder_parameters.copy())

    def reset(self):
        with self.hold_trait_notifications():
            self.process = None
            self.input_structure = None
            self.builder_parameters = self._default_builder_parameters()

    @traitlets.default("builder_parameters")
    def _default_builder_parameters(self):
        return DEFAULT_PARAMETERS


class ViewAtmospecAppWorkChainStatusAndResultsStep(ipw.VBox, WizardAppWidgetStep):

    process = traitlets.Instance(ProcessNode, allow_none=True)

    def __init__(self, **kwargs):
        self.process_tree = ProcessNodesTreeWidget()
        ipw.dlink((self, "process"), (self.process_tree, "process"))

        self.node_view = NodeViewWidget(layout={"width": "auto", "height": "auto"})
        ipw.dlink(
            (self.process_tree, "selected_nodes"),
            (self.node_view, "node"),
            transform=lambda nodes: nodes[0] if nodes else None,
        )
        self.process_status = ipw.VBox(children=[self.process_tree, self.node_view])

        # Setup process monitor
        self.process_monitor = ProcessMonitor(
            timeout=0.1,
            callbacks=[
                self.process_tree.update,
                self._update_state,
            ],
        )
        ipw.dlink((self, "process"), (self.process_monitor, "process"))

        super().__init__([self.process_status], **kwargs)

    def can_reset(self):
        "Do not allow reset while process is running."
        return self.state is not self.State.ACTIVE

    def reset(self):
        self.process = None

    def _update_state(self):
        if self.process is None:
            self.state = self.State.INIT
        else:
            process_state = self.process.process_state
            if process_state in (
                ProcessState.CREATED,
                ProcessState.RUNNING,
                ProcessState.WAITING,
            ):
                self.state = self.State.ACTIVE
            elif process_state in (ProcessState.EXCEPTED, ProcessState.KILLED):
                self.state = self.State.FAIL
            elif process_state is ProcessState.FINISHED:
                self.state = self.State.SUCCESS

    @traitlets.observe("process")
    def _observe_process(self, change):
        self._update_state()


class ViewSpectrumStep(ipw.VBox, WizardAppWidgetStep):

    process = traitlets.Instance(ProcessNode, allow_none=True)

    def __init__(self, **kwargs):
        # Setup process monitor
        self.process_monitor = ProcessMonitor(
            timeout=0.1,
            callbacks=[
                self._show_spectrum,
                self._update_state,
            ],
        )
        self.spectrum = SpectrumWidget()

        ipw.dlink((self, "process"), (self.process_monitor, "process"))

        super().__init__([self.spectrum], **kwargs)

    def reset(self):
        self.process = None
        self.spectrum.reset()

    # TODO: Move this to the workflow
    def _orca_output_to_transitions(self, output_dict, geom_index):
        # TODO: Use atomic units both for energies and osc. strengths
        CM2EV = 1 / 8065.547937
        # TODO: Add error handling
        en = output_dict["etenergies"]
        osc = output_dict["etoscs"]
        assert len(en) == len(osc)
        # TODO: Use atomic units both for energies and osc. strengths
        return [
            {"energy": tr[0] * CM2EV, "osc_strength": tr[1], "geom_index": geom_index}
            for tr in zip(en, osc)
        ]

    def _wigner_output_to_transitions(self, wigner_outputs):
        nsample = len(wigner_outputs)
        transitions = []
        for i, params in zip(range(nsample), wigner_outputs):
            transitions += self._orca_output_to_transitions(params, i)
        return transitions

    def _show_spectrum(self):

        # TODO: Return if process is not finished_ok
        if self.process is None or self.process.process_state != ProcessState.FINISHED:
            return

        # TODO: Handle different kind of computed spectra simultaneously.
        # This is a single-point spectrum
        # output_params = self.process.outputs.single_point_tddft.get_dict()
        # transitions = self._orca_output_to_transitions(output_params, 0)

        # TODO: This is a hack for now until we do a proper Boltzmann weighting.
        conformer_transitions = []
        for conformer in self.process.outputs.spectrum_data.get_list():
            conformer_transitions += self._wigner_output_to_transitions(conformer)

        self.spectrum.transitions = conformer_transitions
        if "smiles" in self.process.inputs.structure.extras:
            self.spectrum.smiles = self.process.inputs.structure.extras["smiles"]
            # We're attaching smiles extra for the optimized structures as well
            # NOTE: You can distinguish between new / optimized geometries
            # by looking at the 'creator' attribute of the Structure node.
            if "relaxed_structures" in self.process.outputs:
                self.process.outputs.relaxed_structures.set_extra(
                    "smiles", self.spectrum.smiles
                )
        else:
            # Resetting smiles in case we already plotted experimental
            # spectrum before.
            self.spectrum.smiles = None

    def _update_state(self):
        if self.process is None:
            self.state = self.State.INIT
            return

        process_state = self.process.process_state
        process_state = self.process.process_state
        if process_state in (
            ProcessState.CREATED,
            ProcessState.RUNNING,
            ProcessState.WAITING,
        ):
            self.state = self.State.ACTIVE
        elif process_state in (ProcessState.EXCEPTED, ProcessState.KILLED):
            self.state = self.State.FAIL
        elif process_state is ProcessState.FINISHED:
            self.state = self.State.SUCCESS

    @traitlets.observe("process")
    def _observe_process(self, change):
        self._update_state()
