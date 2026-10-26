"""Source-checkout entry point for the task migration tool shipped in agent/."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from task_asset_sync import SyncConflict, main, sync_task_assets


if __name__ == '__main__':
    main()
