from pydoover.docker import run_app

from .application import AirMaxApplication


def main():
    """Run the Ubiquiti AirMax auto-config application."""
    run_app(AirMaxApplication())
