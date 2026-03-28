"""ForgeFleet Sub-Agent — thin wrapper that uses the engine. No duplicated logic."""
import sys
import os

# Ensure ForgeFleet is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forgefleet.engine.autonomous import AutonomousWorker

if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/taylorProjects/HireFlow360")
    
    worker = AutonomousWorker(repo_dir=repo)
    worker.run()
