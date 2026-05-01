.PHONY: help build up down restart logs shell test test-fast format lint migrate makemigrations superuser clean

DOCKER_COMPOSE := docker compose
SERVICE_WEB := web
SERVICE_DB := db

.DEFAULT_GOAL := help

help: ## Show this help
	@echo "StickerApp Backend — Make targets"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

build: ## Build images
	$(DOCKER_COMPOSE) build

up: ## Start services in background
	$(DOCKER_COMPOSE) up -d

down: ## Stop services
	$(DOCKER_COMPOSE) down

restart: ## Restart all services
	$(DOCKER_COMPOSE) restart

logs: ## Tail logs from all services
	$(DOCKER_COMPOSE) logs -f

shell: ## Open a Django shell
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) python manage.py shell

dbshell: ## Open psql against the dev database
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) psql -U stickerapp -d stickerapp

test: ## Run the full test suite
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) bash -c "DJANGO_SETTINGS_MODULE=config.settings.test pytest"

test-fast: ## Run tests without coverage
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) bash -c "DJANGO_SETTINGS_MODULE=config.settings.test pytest -q --no-cov"

migrate: ## Apply migrations
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) python manage.py migrate

makemigrations: ## Generate new migrations
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) python manage.py makemigrations

superuser: ## Create a Django superuser
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) python manage.py createsuperuser

format: ## Format code with black + isort
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) bash -c "black . && isort ."

lint: ## Run flake8
	$(DOCKER_COMPOSE) exec $(SERVICE_WEB) flake8 .

clean: ## Stop services and remove volumes (DESTRUCTIVE)
	$(DOCKER_COMPOSE) down -v
