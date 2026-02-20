"""Azure Functions entry point for fx-app-apps-services."""
from __future__ import annotations

# ============================================================
# SECTION 1: Standard library imports
# ============================================================
import logging

# ============================================================
# SECTION 2: Third-party imports
# ============================================================
import azure.functions as func

# ============================================================
# SECTION 3: Seq configuration (BEFORE FunctionApp)
# ============================================================
from functions.shared.seq_logging import configure_seq_logging
configure_seq_logging()

# Get logger after configuration
logger = logging.getLogger(__name__)

# ============================================================
# SECTION 4: Create FunctionApp instance
# ============================================================
app = func.FunctionApp()

# ============================================================
# SECTION 5: Blueprint imports and registrations
# ============================================================
from functions.scheduler.timer_function import bp as scheduler_bp
from functions.master_services_log.status_endpoints import bp as master_services_log_bp
from functions.trigger_function.trigger_function import bp as trigger_bp

app.register_functions(scheduler_bp)
app.register_functions(master_services_log_bp)
app.register_functions(trigger_bp)
