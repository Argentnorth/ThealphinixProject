"""WSGI entry point for the Customer Email Support Automation API."""

from support_automation import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", False))
