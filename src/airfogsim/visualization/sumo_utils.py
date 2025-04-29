"""
SUMO相关的辅助工具函数，用于启动、管理和终止SUMO仿真进程。
"""

import os
import sys
import time
import signal
from typing import Optional
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

# --- SUMO Helper Functions ---
def start_sumo(sumocfg_path, port=8813, gui=False):
    """Starts the SUMO simulation as a TraCI server."""
    sumo_binary = "sumo-gui" if gui else "sumo"
    try:
        # Check if SUMO binary exists and is executable
        subprocess.run([sumo_binary, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.error(f"{sumo_binary} not found or not executable. Please ensure SUMO is installed and in the system PATH.")
        return None
    except PermissionError:
         logger.error(f"Permission denied when trying to execute {sumo_binary}. Check file permissions.")
         return None

    # Ensure absolute path for SUMO config
    if not os.path.isabs(sumocfg_path):
        # Assume relative paths are relative to the project root CWD
        sumocfg_path = os.path.abspath(sumocfg_path)
        logger.info(f"SUMO config path was relative, resolved to absolute path: {sumocfg_path}")

    if not os.path.exists(sumocfg_path):
        logger.error(f"SUMO config file not found: {sumocfg_path}")
        return None
    if not os.path.isfile(sumocfg_path):
        logger.error(f"SUMO config path is not a file: {sumocfg_path}")
        return None

    cmd = [
        sumo_binary,
        "-c", sumocfg_path,
        "--remote-port", str(port),
        "--start", # Automatically start the simulation in SUMO
        "--quit-on-end" # SUMO quits when the simulation ends
        # Add other SUMO options as needed from config, e.g., step-length
        # "--step-length", "0.1"
    ]
    logger.info(f"Starting SUMO: {' '.join(cmd)}")
    try:
        # On Windows, use CREATE_NEW_CONSOLE for GUI to open in a new window
        creationflags = 0
        if sys.platform == 'win32' and gui:
            creationflags = subprocess.CREATE_NEW_CONSOLE # 0x00000010

        # Use preexec_fn=os.setsid on Unix-like systems to run SUMO in a new process group.
        # This allows terminating the entire group (SUMO and any children) reliably.
        preexec_fn = os.setsid if sys.platform != 'win32' else None

        process = subprocess.Popen(
            cmd,
            creationflags=creationflags,
            preexec_fn=preexec_fn,
            stdout=subprocess.PIPE, # Capture stdout
            stderr=subprocess.PIPE  # Capture stderr
        )

        # Give SUMO a moment to start up and potentially fail early
        time.sleep(2) # Consider a more robust check, e.g., trying to connect via TraCI

        # Check if the process terminated unexpectedly right after start
        poll_result = process.poll()
        if poll_result is not None:
             # Read stderr for clues
             stderr_output = process.stderr.read().decode(errors='ignore')
             logger.error(f"SUMO process terminated immediately after start (exit code: {poll_result}). Stderr: {stderr_output}")
             return None

        logger.info(f"SUMO process started successfully (PID: {process.pid})")
        return process
    except FileNotFoundError:
         # This case should be caught by the initial check, but handle defensively
         logger.error(f"Failed to start SUMO: {sumo_binary} command not found.")
         return None
    except PermissionError as e:
         logger.error(f"Failed to start SUMO due to permissions: {e}")
         return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while starting SUMO: {e}", exc_info=True)
        # Clean up if process started but failed later
        if 'process' in locals() and process.poll() is None:
            process.kill()
        return None

def terminate_sumo(process: Optional[subprocess.Popen]):
    """Terminates the SUMO process gracefully, then forcefully if necessary."""
    if not process or process.poll() is not None: # Check if process exists and is running
        logger.debug("SUMO process already terminated or does not exist.")
        return

    pid = process.pid
    logger.info(f"Attempting to terminate SUMO process (PID: {pid})...")
    try:
        if sys.platform == 'win32':
            # Windows: Use taskkill to terminate the process tree (/T) forcefully (/F)
            # This is generally reliable for terminating console applications and their children.
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f"Sent taskkill /F /T to SUMO process (PID: {pid}).")
            # Wait briefly for termination
            process.wait(timeout=3)
        else:
            # Unix-like: Send SIGTERM to the process group using the PGID obtained via os.setsid
            pgid = os.getpgid(pid)
            logger.info(f"Sending SIGTERM to SUMO process group (PGID: {pgid})...")
            os.killpg(pgid, signal.SIGTERM)
            # Wait for graceful termination
            process.wait(timeout=5)

        logger.info(f"SUMO process (PID: {pid}) terminated gracefully.")

    except subprocess.TimeoutExpired:
        logger.warning(f"SUMO process (PID: {pid}) did not terminate gracefully within timeout. Forcing kill...")
        try:
            if sys.platform == 'win32':
                # Force kill using Popen.kill() as a fallback
                 process.kill()
            else:
                # Send SIGKILL to the process group
                 pgid = os.getpgid(pid) # Get pgid again just in case
                 logger.info(f"Sending SIGKILL to SUMO process group (PGID: {pgid})...")
                 os.killpg(pgid, signal.SIGKILL)

            # Wait briefly for kill signal to take effect
            process.wait(timeout=2)
            logger.info(f"SUMO process (PID: {pid}) force killed.")
        except ProcessLookupError:
             # This can happen if the process died between the timeout and the kill attempt
             logger.warning(f"Process group for PID {pid} not found during SIGKILL (likely already terminated).")
        except Exception as kill_err:
             logger.error(f"Error sending SIGKILL to process group for PID {pid}: {kill_err}")

    except subprocess.CalledProcessError as e:
         # taskkill might fail if the process already exited
         logger.warning(f"taskkill command failed for PID {pid} (maybe already exited?): {e.stderr.decode(errors='ignore')}")
    except ProcessLookupError:
         # This can happen if the process died between the start of terminate_sumo and os.killpg
         logger.warning(f"Process or process group for PID {pid} not found during SIGTERM (likely already terminated).")
    except Exception as e:
        logger.error(f"An unexpected error occurred during SUMO termination (PID: {pid}): {e}", exc_info=True)
        # Final attempt to kill if it's somehow still alive
        if process.poll() is None:
            logger.info(f"Final attempt to kill SUMO process (PID: {pid})...")
            process.kill()
            process.wait(timeout=1)

    # Final check
    if process.poll() is None:
        logger.error(f"Failed to terminate SUMO process (PID: {pid}) after all attempts.")
    else:
        logger.debug(f"SUMO process (PID: {pid}) confirmed terminated with exit code {process.poll()}.")
