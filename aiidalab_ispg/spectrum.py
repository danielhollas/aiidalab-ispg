"""Widget for displaying UV/VIS spectra in interactive graphs

Authors:
    * Daniel Hollas <daniel.hollas@durham.ac.uk>
"""
import ipywidgets as ipw
import traitlets
import scipy
from scipy import constants
import numpy as np

from aiida.orm import QueryBuilder
from aiida.plugins import DataFactory

XyData = DataFactory("array.xy")

# TODO: Just pick one renderer to simplify all this mess.
# Bokeh looks nicest by default and is fast out of the box.
# RENDERER = 'MATPLOTLIB'
RENDERER = "BOKEH"
# RENDERER = 'BQPLOT'

if RENDERER == "MATPLOTLIB":
    import matplotlib.pyplot as plt
elif RENDERER == "BQPLOT":
    import bqplot.pyplot as plt

    # https://coderzcolumn.com/tutorials/data-science/interactive-plotting-in-python-jupyter-notebook-using-bqplot#3
elif RENDERER == "BOKEH":
    # https://docs.bokeh.org/en/latest/docs/user_guide/jupyter.html
    # https://github.com/bokeh/bokeh/blob/branch-3.0/examples/howto/server_embed/notebook_embed.ipynb
    # https://github.com/bokeh/bokeh/blob/branch-3.0/examples/howto/server_embed/notebook_embed.ipynb
    from bokeh.io import push_notebook, show, output_notebook
    import bokeh.plotting as plt

    output_notebook()


class BokehFigureContext(ipw.Output):
    def __init__(self, fig):
        super().__init__()
        self._figure = fig
        self.on_displayed(lambda x: x.set_handle())

    def set_handle(self):
        self.clear_output()
        with self:
            self._handle = show(self._figure, notebook_handle=True)

    def get_handle(self):
        return self._handle

    def get_figure(self):
        return self._figure

    def update(self):
        push_notebook(handle=self._handle)


class Spectrum(object):
    AUtoCm = 8.478354e-30
    COEFF = (
        constants.pi
        * AUtoCm ** 2
        * 1e4
        / (3 * scipy.constants.hbar * scipy.constants.epsilon_0 * scipy.constants.c)
    )
    # Transition Dipole to Osc. Strength in atomic units
    COEFF_NEW = COEFF * 3 / 2
    # COEFF =  scipy.constants.pi * AUtoCm**2 * 1e4 * scipy.constants.hbar / (2 * scipy.constants.epsilon_0 * scipy.constants.c * scipy.constants.m_e)

    def __init__(self, transitions, nsample):
        # Excitation energies in eV
        self.excitation_energies = np.array(
            [tr["energy"] for tr in transitions], dtype=float
        )
        # Oscillator strengths
        self.osc_strengths = np.array(
            [tr["osc_strength"] for tr in transitions], dtype=float
        )
        # Number of molecular geometries sampled from ground state distribution
        self.nsample = nsample

    # TODO
    def get_spectrum(self, x_min, x_max, x_units, y_units):
        """Returns a non-broadened spectrum as a tuple of x and y Numpy arrays"""
        n_points = int((x_max - x_min) / self.de)
        x = np.arange(x_min, x_max, self.de)
        y = np.zeros(n_points)
        return x, y

    # TODO: Make this function aware of units?
    def _get_energy_range(self, energy_unit):
        # NOTE: We don't include zero to prevent
        # division by zero when converting to wavelength
        x_min = max(0.01, self.excitation_energies.min() - 2.0)
        x_max = self.excitation_energies.max() + 2.0

        # conversion to nanometers is handled later
        if energy_unit.lower() == "nm":
            return x_min, x_max

        # energy_factor_unit = self._get_energy_unit_factor(energy_unit)
        # x_min *= energy_factor_unit
        # x_max *= energy_factor_unit
        return x_min, x_max

    # TODO: Define energy units as Enum in this file.
    # TODO: Put this function outside of this class
    # So that it can be useful for experimental spectrum as well
    def _get_energy_unit_factor(self, unit):
        # TODO: We should probably start from atomic units
        if unit.lower() == "ev":
            return 1.0
        # TODO: Construct these factors from scipy.constants
        elif unit.lower() == "nm":
            return 1239.8
        elif unit.lower() == "cm^-1":
            return 8065.7

    def get_gaussian_spectrum(self, sigma, x_unit, y_unit):
        """Returns Gaussian broadened spectrum"""

        x_min, x_max = self._get_energy_range(x_unit)

        # Conversion factor from eV to given energy unit
        # (should probably switch to atomic units as default)
        energy_unit_factor = self._get_energy_unit_factor(x_unit)

        energies = np.copy(self.excitation_energies)
        # Conversion to wavelength in nm is done at the end instead
        # Since it's not a linear transformation
        # if x_unit.lower() != 'nm':
        #    sigma *= energy_unit_factor
        #    energies *= energy_unit_factor

        # TODO: How to determine this properly to cover a given interval?
        n_sample = 500
        x = np.linspace(x_min, x_max, num=n_sample)
        y = np.zeros(len(x))

        normalization_factor = (
            1 / np.sqrt(2 * scipy.constants.pi) / sigma / self.nsample
        )
        # TODO: Support other intensity units
        unit_factor = self.COEFF_NEW
        for exc_energy, osc_strength in zip(energies, self.osc_strengths):
            prefactor = normalization_factor * unit_factor * osc_strength
            y += prefactor * np.exp(-((x - exc_energy) ** 2) / 2 / sigma ** 2)

        if x_unit.lower() == "nm":
            x, y = self._convert_to_nanometers(x, y)
        else:
            x *= energy_unit_factor

        return x, y

    def get_lorentzian_spectrum(self, tau, x_unit, y_unit):
        """Returns Gaussian broadened spectrum"""
        # TODO: Determine x_min automatically based on transition energies
        # and x_units
        x_min, x_max = self._get_energy_range(x_unit)

        # Conversion factor from eV to given energy unit
        # (should probably switch to atomic units as default)
        energy_unit_factor = self._get_energy_unit_factor(x_unit)

        energies = np.copy(self.excitation_energies)

        # TODO: How to determine this properly to cover a given interval?
        n_sample = 500
        x = np.linspace(x_min, x_max, num=n_sample)
        y = np.zeros(len(x))

        normalization_factor = tau / 2 / scipy.constants.pi / self.nsample
        unit_factor = self.COEFF_NEW

        for exc_energy, osc_strength in zip(energies, self.osc_strengths):
            prefactor = normalization_factor * unit_factor * osc_strength
            y += prefactor / ((x - exc_energy) ** 2 + (tau ** 2) / 4)

        if x_unit.lower() == "nm":
            x, y = self._convert_to_nanometers(x, y)
        else:
            x *= energy_unit_factor

        return x, y

    def _convert_to_nanometers(self, x, y):
        x = self._get_energy_unit_factor("nm") / x
        # Filtering out ultralong wavelengths
        # TODO: Generalize this based on self.transitions
        nm_thr = 1000
        to_delete = []
        for i in range(len(x)):
            if x[i] > nm_thr:
                to_delete.append(i)
        x = np.delete(x, to_delete)
        y = np.delete(y, to_delete)
        return x, y


class SpectrumWidget(ipw.VBox):

    transitions = traitlets.List()
    # We use SMILES to find matching experimental spectra
    # that are possibly stored in our DB as XyData.
    smiles = traitlets.Unicode()
    experimental_spectrum = traitlets.Instance(XyData, allow_none=True)

    # For now, we do not allow different intensity units
    intensity_unit = "cm^2 per molecule"

    def __init__(self, **kwargs):
        title = ipw.HTML(
            """<div style="padding-top: 0px; padding-bottom: 0px">
            <h4>UV/Vis Spectrum</h4></div>"""
        )

        self.width_slider = ipw.FloatSlider(
            min=0.05, max=1, step=0.05, value=0.5, description="Width / eV"
        )
        self.kernel_selector = ipw.ToggleButtons(
            options=["gaussian", "lorentzian"],  # TODO: None option
            description="Broadening kernel:",
            disabled=False,
            button_style="info",  # 'success', 'info', 'warning', 'danger' or ''
            tooltips=[
                "Description of slow",
                "Description of regular",
                "Description of fast",
            ],
        )
        self.energy_unit_selector = ipw.RadioButtons(
            # TODO: Make an enum with different energy units
            options=["eV", "nm", "cm^-1"],
            disabled=False,
            description="Energy unit",
        )

        controls = ipw.HBox(
            children=[
                ipw.VBox(children=[self.kernel_selector, self.width_slider]),
                self.energy_unit_selector,
            ]
        )

        self._init_figure()
        self.spectrum_container = ipw.Box()
        if RENDERER == "BOKEH":
            # TODO: Convert other renderers to this as well,
            # or get rid of them.
            self.spectrum_container.children = [self.figure]
            self.kernel_selector.observe(self._handle_kernel_update, names="value")
            self.energy_unit_selector.observe(self._handle_energy_unit_update, names="value")
            self.width_slider.observe(self._handle_width_update, names="value")

        super().__init__(
            [
                title,
                controls,
                self.spectrum_container,
            ],
            **kwargs,
        )

    def _handle_width_update(self, change):
        """Redraw spectra when user changes broadening width via slider"""
        # TODO: We don't need to redraw axis labels in this case
        width = change['new']
        self._plot_spectrum(
            width=width,
            kernel=self.kernel_selector.value,
            energy_unit=self.energy_unit_selector.value,
        )
        self.figure.update()

    def _handle_kernel_update(self, change):
        """Redraw spectra when user changes kernel for broadening"""
        kernel = change['new']
        self._plot_spectrum(
            width=self.width_slider.value,
            kernel=kernel,
            energy_unit=self.energy_unit_selector.value,
        )
        self.figure.update()

    def _handle_energy_unit_update(self, change):
        """Updates the spectrum when user changes energy units
           In this case, we also redraw experimental spectra, if available."""

        energy_unit = change['new']
        xlabel = f"Energy / {energy_unit}"
        self.figure.get_figure().xaxis.axis_label = xlabel

        self._plot_spectrum(
            width=self.width_slider.value,
            kernel=self.kernel_selector.value,
            energy_unit=energy_unit,
        )
        if self.experimental_spectrum is not None:
            self._plot_experimental_spectrum(
                spectrum_node=self.experimental_spectrum,
                energy_unit=energy_unit
            )
        self.figure.update()

    def _plot_spectrum(self, kernel, width, energy_unit):
        if not self._validate_transitions():
            return
        nsample = 1
        spec = Spectrum(self.transitions, nsample)
        if kernel == "lorentzian":
            x, y = spec.get_lorentzian_spectrum(width, energy_unit, self.intensity_unit)
        elif kernel == "gaussian":
            x, y = spec.get_gaussian_spectrum(width, energy_unit, self.intensity_unit)
        else:
            print("Invalid broadening type")
            return

        # Matplotlib with ipywidgets
        # https://kapernikov.com/ipywidgets-with-matplotlib/

        # Remove previous lines.
        # This does not seem to be needed for matplotlib,
        # but somehow needed for bqplot.
        # [l.remove() for l in self.axes.lines]
        # Note for improving performance when using matplotlib
        # https://matplotlib.org/stable/tutorials/advanced/blitting.html#sphx-glr-tutorials-advanced-blitting-py
        if RENDERER == "BOKEH":
            # Rendering by BOKEH by
            f = self.figure.get_figure()
            line = f.select_one({'name': 'theory'})
            line.data_source.data = {"x": x, "y": y}
        else:
            plt.plot(x, y)
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            plt.show()

    def _validate_transitions(self):
        # TODO: Maybe use named tuple instead of dictionary?
        # We should probably make a traitType for this and export it.
        # https://realpython.com/python-namedtuple/
        if len(self.transitions) == 0:
            return False

        for tr in self.transitions:
            if not isinstance(tr, dict) or (
                "energy" not in tr or "osc_strength" not in tr
            ):
                print("Invalid transition", tr)
                return False
        return True

    def _init_figure(self, *args, **kwargs):
        self.figure = BokehFigureContext(plt.figure(*args, **kwargs))
        f = self.figure.get_figure()
        f.xaxis.axis_label = f"Energy / {self.energy_unit_selector.value}"
        f.yaxis.axis_label = f"Intensity / {self.intensity_unit}"

        # Initialize lines for theoretical and possible experimental spectra
        # NOTE: Hardly earned experience: It is crucial that both lines
        # are initiated here, before the figure is first shown. Otherwise,
        # apparently updates via line.data_source are not picked up.
        x = np.array([0.0, 1.0])
        y = np.copy(x)
        # TODO: Choose inclusive colors!
        f.line(x, y, line_width=2, name='theory')
        l = f.line(
                x, y, line_width=2, line_dash='dashed', line_color='orange',
                name='experiment'
        )
        # Experimental spectrum only available for some molecules
        l.visible = False

    def _show_spectrum(self):
        if not self._validate_transitions:
            # TODO: Add proper error handling
            raise KeyError

        if RENDERER == "BOKEH":
            self._plot_spectrum(
                width=self.width_slider.value,
                kernel=self.kernel_selector.value,
                energy_unit=self.energy_unit_selector.value,
            )
            self.figure.update()
        else:
            spectrum = ipw.interactive_output(
                self._plot_spectrum,
                {
                    "width": self.width_slider,
                    "kernel": self.kernel_selector,
                    "energy_unit": self.energy_unit_selector,
                },
            )
            self.spectrum_container.children = [spectrum]

    @traitlets.observe("transitions")
    def _observe_transitions(self, change):
        self._show_spectrum()

    @traitlets.observe("smiles")
    def _observe_smiles(self, change):
        self._find_experimental_spectrum(change['new'])

    # TODO: Put the experimental spectrum stuff in its own class?
    def _find_experimental_spectrum(self, smiles):
        qb = QueryBuilder()
        qb.append(XyData, filters={"extras.smiles": smiles})

        if qb.count() == 0:
            # TODO: Remove this debug print
            print(f"No experimental spectrum available for SMILES {smiles}")
            return

        # for spectrum in qb.iterall():
        # TODO: For now let's just assume we have one canonical experiment
        self.experimental_spectrum = qb.first()[0]
        self._plot_experimental_spectrum(
            spectrum_node=self.experimental_spectrum,
            energy_unit=self.energy_unit_selector.value,
        )
        self.figure.update()

    def _plot_experimental_spectrum(self, spectrum_node, energy_unit):
        """Render experimental spectrum that was loaded to AiiDA database manually
        param: spectrum_node: XyData node"""
        # TODO: When we're creating spectrum as XyData,
        # can we choose nicer names for x and y?
        try:
            # TODO: Extract units as well.
            # TODO: Do this extraction only once for performance
            # when switching units.
            energy = spectrum_node.get_array("x_array")
            cross_section = spectrum_node.get_array("y_array_0")
        except:
            print("ERROR: Could not extract experimental spectra from DB")

        # TODO: Refactor how units are handled in this file for forks sake!
        # We should decouple changing units from spectra plotting in general
        # (e.g. do not recalculate the intensity of theoretical spectrum
        # when units are changed!
        if energy_unit.lower() == "ev":
            energy = 1239.8 / energy
        elif energy_unit.lower() == "cm^-1":
            energy = 8065.7 * 1239.8 / energy

        # https://docs.bokeh.org/en/latest/docs/reference/models/renderers.html?highlight=renderers#renderergroup
        f = self.figure.get_figure()
        line = f.select_one({'name': 'experiment'})
        line.visible = True
        line.data_source.data = {"x": energy, "y": cross_section}
