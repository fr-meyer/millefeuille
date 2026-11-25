"""Explicit ordinary native routes; preserve canonical legacy CLI fallback."""
import sys
import os
from importlib import import_module

ROUTES = {'process': ('standalone', 'run_process_cli'), 'clean-start': ('clean_start', 'run_clean_start_cli'), 'final-classification': ('final_classification', 'run_final_classification_cli'), 'standalone-retention': ('standalone_retention', 'run_standalone_retention_cli'), 'paper-card': ('paper_cards', 'run_paper_card_cli'), 'taxonomy-store': ('taxonomy_store', 'run_taxonomy_store_cli'), 'decisions-classify': ('decisions_classification', 'run_decisions_classification_cli'), 'standalone-writeback': ('standalone_writeback', 'run_standalone_writeback_cli')}

def entrypoint():
    args = sys.argv[1:]
    if args and args[0] in ROUTES:
        # Profile selection is not credential authority. The actual CLI parser
        # still determines operation/stages before command_credential_scope.
        # Let argparse own help, without changing scientific execution state.
        if "--help" not in args[1:] and "-h" not in args[1:]:
            profile = os.environ.setdefault(
                "MILLEFEUILLE_EXECUTION_PROFILE", "native-sdk-bridge/v1"
            )
            if profile != "native-sdk-bridge/v1":
                print("ORDINARY_NATIVE_PROFILE_REQUIRED", file=sys.stderr)
                return 2
        module, function = ROUTES[args[0]]
        return getattr(import_module("millefeuille.cli." + module), function)(args[1:])
    from millefeuille.cli.main import entrypoint as legacy_entrypoint
    return legacy_entrypoint()
