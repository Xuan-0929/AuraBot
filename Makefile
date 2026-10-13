# Documents the desktop configuration entry point without creating or overwriting files.
# Electron creates settings.json on first launch; users configure it in the Settings window.
# The legacy `make config` command remains as guidance for existing users.
.PHONY: config

config:
	@echo "AuraBot creates settings.json on first launch. Configure the app in Settings, then Save and Apply."
