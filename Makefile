.PHONY: dev-frontend dev-backend dev build clean

dev-frontend:
	cd frontend && npm install && npm run dev

dev-backend:
	cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000

dev:
	@echo "Run in two terminals:"
	@echo "  Terminal 1: make dev-backend"
	@echo "  Terminal 2: make dev-frontend"
	@echo "Then open http://localhost:4444"

build-frontend:
	cd frontend && npm run build

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

clean:
	rm -f backend/dev.db