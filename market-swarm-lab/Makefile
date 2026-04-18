setup:
	@if [ ! -f .env ]; then cp .env.example .env; fi
	docker compose build

run:
	docker compose up --build postgres redis api collector forecasting reporting seed-builder mirofish-bridge

demo:
	docker compose run --rm api python -m apps.api.demo NVDA SPY
