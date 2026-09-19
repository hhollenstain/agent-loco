from agent_loco.cli import app as cli_app
from agent_loco.web_ui import app as web_ui_app
import sys

if '--web-ui' in sys.argv:
    web_ui_app.run(debug=True)
else:
    cli_app()