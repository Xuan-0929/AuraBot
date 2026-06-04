.PHONY: config

config:
	cp -f .env.example .env
	cp -f config.example.yaml config.yaml
	@echo "Created .env and config.yaml from example files."
