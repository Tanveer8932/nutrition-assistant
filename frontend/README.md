# Nutrition Assistant: frontend

Angular chat UI for the Nutrition Assistant: message list, composer, and a sources panel.
The sources panel stays empty in Milestone 1 and gets filled by the retrieval layer in Milestone 2.

```bash
npm install
npm start        # http://localhost:4200, /api is proxied to http://localhost:8000 (proxy.conf.json)
npm run build    # production build to dist/frontend/browser
```

The browser only calls the backend's `/api/*` endpoints. The model API key stays on the server.
See the root `README.md` for the backend, deployment, and evaluation.
