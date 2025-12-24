from .tracker import TimeTracker

# Global, single tracker instance (like loguru.logger)
time_tracker = TimeTracker()

__all__ = ["TimeTracker", "time_tracker"]
