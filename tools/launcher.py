from ams_recorder.gui import main
from ams_recorder.updates import helper_main
from ams_recorder.installation import running, refresh_display_version
from ams_recorder import __version__
import sys

if __name__ == "__main__":
    with running():
        if sys.argv[1:2] == ['--apply-update']:
            helper_main()
        else:
            if '--smoke-test' not in sys.argv:
                refresh_display_version(sys.executable, __version__)
            main()
