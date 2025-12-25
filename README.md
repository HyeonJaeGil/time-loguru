# Python package for time tracking with Loguru

## Usage

```bash

from loguru import logger
from time_loguru import time_logger

time_logger.configure(emit_each=True)
time_logger.add_event_sink(FILE_TO_SAVE_TIME_TRACKING_LOGS)

with time_logger.info("LOAD_DATA"):
    ...  # work to be measured

time_logger.summary()

```
