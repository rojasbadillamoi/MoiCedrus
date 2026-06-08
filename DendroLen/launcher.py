import sys
import os
import traceback

log_path = os.path.join(os.path.dirname(sys.executable), "crash_log.txt")

try:
    from main_dendro import main
    main()
except SystemExit:
    pass
except Exception:
    with open(log_path, "w") as f:
        traceback.print_exc(file=f)