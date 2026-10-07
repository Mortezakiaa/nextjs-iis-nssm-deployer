"""The tool's own operation log (C:\\ProgramData\\NextDeployer\\deployer.log). Technical details go here, not to the screen."""
import logging
from logging.handlers import RotatingFileHandler

import config

log = logging.getLogger("nextdeployer")
log.setLevel(logging.INFO)
log.addHandler(logging.NullHandler())


def setup() -> None:
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(config.LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return  # no log file is better than no tool
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
