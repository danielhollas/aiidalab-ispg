from aiidalab_ispg.process import WorkChainSelector
from aiidalab_ispg.widgets import TrajectoryDataViewer
from aiidalab_ispg.structures import StructureSelectionStep
from aiidalab_ispg.steps import (
    SubmitAtmospecAppWorkChainStep,
    ViewAtmospecAppWorkChainStatusAndResultsStep,
    ViewSpectrumStep,
)

__all__ = [
    "TrajectoryDataViewer",
    "WorkChainSelector",
    "StructureSelectionStep",
    "SubmitAtmospecAppWorkChainStep",
    "ViewAtmospecAppWorkChainStatusAndResultsStep",
    "ViewSpectrumStep",
]

# WARNING: This needs to be kept in sync with version
# in setup.cfg
# TODO: Take a look and how aiidalab-qe and other packages do it.
__version__ = "0.1-alpha"
