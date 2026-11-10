from ams_recorder.gui import main
from ams_recorder.updates import helper_main
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ['--apply-update']:
        helper_main()
    else:
        main()
