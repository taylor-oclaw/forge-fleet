"""ForgeFleet Sub-Agent + Node Manager — builds tickets AND manages the node."""
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forgefleet.engine.autonomous import AutonomousWorker
from forgefleet.engine.node_manager import NodeManager

if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/taylorProjects/HireFlow360")
    
    # Start node manager in background (self-heal + report status)
    node_mgr = NodeManager()
    monitor_thread = threading.Thread(target=node_mgr.run_monitor, args=(60,), daemon=True)
    monitor_thread.start()
    
    # Run the autonomous worker (builds tickets)
    worker = AutonomousWorker(repo_dir=repo)
    worker.run()
