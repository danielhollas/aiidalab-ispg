"""Widgets for the QE app.

Authors:

    * Carl Simon Adorf <simon.adorf@epfl.ch>
"""

import base64
from queue import Queue
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread

import ipywidgets as ipw
import traitlets
import nglview
from aiida.orm import CalcJobNode, Node
from aiidalab_widgets_base import register_viewer_widget, viewer
from aiidalab_widgets_base.viewers import StructureDataViewer
from IPython.display import clear_output, display

from aiida.plugins import DataFactory

TrajectoryData = DataFactory("array.trajectory")

__all__ = [
    "CalcJobOutputFollower",
    "LogOutputWidget",
    "NodeViewWidget",
    "TrajectoryDataViewer",
]


class LogOutputWidget(ipw.VBox):

    value = traitlets.Tuple(traitlets.Unicode(), traitlets.Unicode())

    def __init__(self, num_min_lines=10, max_output_height="200px", **kwargs):
        self._num_min_lines = num_min_lines

        self._filename = ipw.Text(
            description="Filename:",
            placeholder="No file selected.",
            disabled=True,
            layout=ipw.Layout(flex="1 1 auto", width="auto"),
        )

        self._output = ipw.HTML(layout=ipw.Layout(min_width="50em"))
        self._output_container = ipw.VBox(
            children=[self._output], layout=ipw.Layout(max_height=max_output_height)
        )
        self._download_link = ipw.HTML(layout=ipw.Layout(width="auto"))

        self._refresh_output()
        super().__init__(
            children=[
                self._output_container,
                ipw.HBox(
                    children=[self._filename, self._download_link],
                    layout=ipw.Layout(min_height="25px"),
                ),
            ],
            **kwargs,
        )

    @traitlets.default("value")
    def _default_value(self):
        if self._num_min_lines > 0:
            return "", "\n" * self._num_min_lines

    @traitlets.observe("value")
    def _refresh_output(self, _=None):
        filename, loglines = self.value
        with self.hold_trait_notifications():
            self._filename.value = filename
            self._output.value = self._format_output(loglines)

            payload = base64.b64encode(loglines.encode()).decode()
            html_download = f'<a download="{filename}" href="data:text/plain;base64,{payload}" target="_blank">Download</a>'
            self._download_link.value = html_download

    style = "background-color: #253239; color: #cdd3df; line-height: normal"

    def _format_output(self, text):
        lines = text.splitlines()

        # Add empty lines to reach the minimum number of lines.
        lines += [""] * max(0, self._num_min_lines - len(lines))

        # Replace empty lines with single white space to ensure that they are actually shown.
        lines = [line if len(line) > 0 else " " for line in lines]

        # Replace the first line if there is no output whatsoever
        if len(text.strip()) == 0 and len(lines) > 0:
            lines[0] = "[waiting for output]"

        text = "\n".join(lines)
        return f"""<pre style="{self.style}">{text}</pre>"""


class CalcJobOutputFollower(traitlets.HasTraits):

    calcjob = traitlets.Instance(CalcJobNode, allow_none=True)
    filename = traitlets.Unicode(allow_none=True)
    output = traitlets.List(trait=traitlets.Unicode)
    lineno = traitlets.Int()

    def __init__(self, **kwargs):
        self._output_queue = Queue()

        self._lock = Lock()
        self._push_thread = None
        self._pull_thread = None
        self._stop_follow_output = Event()
        self._follow_output_thread = None

        super().__init__(**kwargs)

    @traitlets.observe("calcjob")
    def _observe_calcjob(self, change):
        try:
            if change["old"].pk == change["new"].pk:
                # Old and new process are identical.
                return
        except AttributeError:
            pass

        with self._lock:
            # Stop following
            self._stop_follow_output.set()

            if self._follow_output_thread:
                self._follow_output_thread.join()
                self._follow_output_thread = None

            # Reset all traitlets and signals.
            self.output.clear()
            self.lineno = 0
            self._stop_follow_output.clear()

            # (Re/)start following
            if change["new"]:
                self._follow_output_thread = Thread(
                    target=self._follow_output, args=(change["new"],)
                )
                self._follow_output_thread.start()

    def _follow_output(self, calcjob):
        """Monitor calcjob and orchestrate pushing and pulling of output."""
        self._pull_thread = Thread(target=self._pull_output, args=(calcjob,))
        self._pull_thread.start()
        self._push_thread = Thread(target=self._push_output, args=(calcjob,))
        self._push_thread.start()

    def _fetch_output(self, calcjob):
        assert isinstance(calcjob, CalcJobNode)
        if "remote_folder" in calcjob.outputs:
            try:
                fn_out = calcjob.attributes["output_filename"]
                self.filename = fn_out
                with NamedTemporaryFile() as tmpfile:
                    calcjob.outputs.remote_folder.getfile(fn_out, tmpfile.name)
                    return tmpfile.read().decode().splitlines()
            except OSError:
                return list()
        else:
            return list()

    _EOF = None

    def _push_output(self, calcjob, delay=0.2):
        """Push new log lines onto the queue."""
        lineno = 0
        while True:
            try:
                lines = self._fetch_output(calcjob)
            except Exception as error:
                self._output_queue.put([f"[ERROR: {error}]"])
            else:
                self._output_queue.put(lines[lineno:])
                lineno = len(lines)
            finally:
                if calcjob.is_sealed or self._stop_follow_output.wait(delay):
                    # Pushing EOF signals to the pull thread to stop.
                    self._output_queue.put(self._EOF)
                    break

    def _pull_output(self, calcjob):
        """Pull new log lines from the queue and update traitlets."""
        while True:
            item = self._output_queue.get()
            if item is self._EOF:
                self._output_queue.task_done()
                break
            else:  # item is 'new lines'
                with self.hold_trait_notifications():
                    self.output.extend(item)
                    self.lineno += len(item)
                self._output_queue.task_done()


@register_viewer_widget("process.calculation.calcjob.CalcJobNode.")
class CalcJobNodeViewerWidget(ipw.VBox):
    def __init__(self, calcjob, **kwargs):
        self.calcjob = calcjob
        self.output_follower = CalcJobOutputFollower()
        self.log_output = LogOutputWidget()

        self.output_follower.observe(self._observe_output_follower_lineno, ["lineno"])
        self.output_follower.calcjob = self.calcjob

        super().__init__(
            [ipw.HTML(f"CalcJob: {self.calcjob}"), self.log_output], **kwargs
        )

    def _observe_output_follower_lineno(self, change):
        restrict_num_lines = None if self.calcjob.is_sealed else -10
        new_lines = "\n".join(self.output_follower.output[restrict_num_lines:])
        self.log_output.value = self.output_follower.filename, new_lines


@register_viewer_widget("data.array.trajectory.TrajectoryData.")
class TrajectoryDataViewer(StructureDataViewer):

    trajectory = traitlets.Instance(Node, allow_none=True)
    _structures = []
    _energies = None

    def __init__(self, trajectory=None, **kwargs):
        # Trajectory navigator.
        self._step_selector = ipw.IntSlider(
            min=1,
            max=1,
            disabled=True,
            description="Frame:",
        )
        self._step_selector.observe(self.update_selection, names="value")

        # Display energy if available
        self._energy = ipw.HTML(
            value="Energy = ",
            placeholder="Energy",
        )

        children = [self._step_selector, self._energy]

        super().__init__(
            children=children, configuration_tabs=["Selection", "Download"], **kwargs
        )

        self.trajectory = trajectory

    def update_selection(self, change):
        """Display selected structure"""
        index = change["new"] - 1
        self.structure = self._structures[index]
        # TODO: We should pass energy units as well somehow
        if self._energies is not None:
            self._energy.value = f"Energy = {self._energies[index]:.2f} eV"

    @traitlets.observe("trajectory")
    def _update_trajectory(self, change):
        trajectory = change["new"]
        if trajectory is None:
            self._step_selector.min = 1
            self._step_selector.max = 1
            self._step_selector.disabled = True
            self._step_selector.layout.visibility = "hidden"
            self._energy.layout.visibility = "hidden"
            return

        if isinstance(trajectory, TrajectoryData):
            self._structures = [
                trajectory.get_step_structure(i) for i in self.trajectory.get_stepids()
            ]
            if "energies" in trajectory.get_arraynames():
                self._energies = trajectory.get_array("energies")
                self._energy.layout.visibility = "visible"
                self._energy.value = f"Energy = {self._energies[0]:.2f} eV"
            else:
                self._energies = None
                self._energy.layout.visibility = "hidden"
        else:
            self._structures = [trajectory]

        nframes = len(self._structures)
        self._step_selector.max = nframes
        if nframes == 1:
            self.structure = self._structures[0]
            self._step_selector.layout.visibility = "hidden"
        else:
            self._step_selector.layout.visibility = "visible"
            self._step_selector.disabled = False
            # For some reason, this does not trigger observer
            # if this value was already there, so we update manually
            if self._step_selector.value == 1:
                self.structure = self._structures[0]
            else:
                self._step_selector.value = 1

    # Slightly modified from StructureDataViewer for performance
    @traitlets.observe("displayed_structure")
    def _update_structure_viewer(self, change):
        """Update the view if displayed_structure trait was modified."""
        with self.hold_trait_notifications():
            for (
                comp_id
            ) in self._viewer._ngl_component_ids:  # pylint: disable=protected-access
                self._viewer.remove_component(comp_id)
            self.selection = list()
            if change["new"] is not None:
                self._viewer.add_component(nglview.ASEStructure(change["new"]))


class NodeViewWidget(ipw.VBox):

    node = traitlets.Instance(Node, allow_none=True)

    def __init__(self, **kwargs):
        self._output = ipw.Output()
        super().__init__(children=[self._output], **kwargs)

    @traitlets.observe("node")
    def _observe_node(self, change):
        if change["new"] != change["old"]:
            with self._output:
                clear_output()
                if change["new"]:
                    display(viewer(change["new"]))


class ResourceSelectionWidget(ipw.VBox):
    """Widget for the selection of compute resources."""

    title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>Resources</h4>
    </div>"""
    )
    prompt = ipw.HTML(
        """<div style="line-height:120%; padding-top:0px">
        <p style="padding-bottom:10px">
        Specify the number of MPI tasks for this calculation.
        In general, larger structures will require a larger number of tasks.
        </p></div>"""
    )

    def __init__(self, **kwargs):
        extra = {
            "style": {"description_width": "150px"},
            # "layout": {"max_width": "200px"},
            "layout": {"min_width": "310px"},
        }

        self.num_mpi_tasks = ipw.BoundedIntText(
            value=1, step=1, min=1, description="# MPI tasks", **extra
        )

        super().__init__(
            children=[
                self.title,
                ipw.HBox(children=[self.prompt, self.num_mpi_tasks]),
            ]
        )

    def reset(self):
        self.num_mpi_tasks.value = 1


class QMSelectionWidget(ipw.VBox):
    """Widget for selecting ab initio level (basis set, method, etc.)"""

    qm_title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>QM method selection</h4>
        </div>"""
    )

    spectra_title = ipw.HTML(
        """<div style="padding-top: 0px; padding-bottom: 0px">
        <h4>Spectrum settings</h4>
        </div>"""
    )

    spectra_desc = ipw.HTML(
        """<div style="line-height:120%; padding-top:0px">
        <p style="padding-bottom:10px">
        Settings for modeling UV/VIS spectrum
        </p></div>"""
    )

    def __init__(self, **kwargs):
        style = {"description_width": "initial"}

        self.method = ipw.Text(
            value="pbe",
            description="DFT functional",
            placeholder="Type DFT functional",
            style=style,
        )

        self.basis = ipw.Text(
            value="def2-svp", description="Basis set", placeholder="Type Basis Set"
        )

        self.nwigner = ipw.BoundedIntText(
            value=1,
            step=1,
            min=0,
            max=1000,
            style=style,
            description="Number of Wigner samples",
        )

        super().__init__(
            children=[
                self.qm_title,
                ipw.HBox(children=[self.method, self.basis]),
                self.spectra_title,
                self.spectra_desc,
                self.nwigner,
            ]
        )

    def reset(self):
        self.method = "pbe"
        self.basis = "def2-svp"
        self.nwigner = 1
