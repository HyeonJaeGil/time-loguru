# Python package for time consumption tracker

## Usage

```bash

from loguru import logger
from time_consumption_tracker import time_tracker

time_tracker.configure(emit_each=True)   # emit a line for each completed task
time_tracker.add_event_sink(FILE_TO_SAVE_TIME_TRACKING_LOGS) # optional

with time_tracker.info("LOAD_DATA"):
    ...  # work to be measured

time_tracker.summary()                   # prints summary through loguru

```
