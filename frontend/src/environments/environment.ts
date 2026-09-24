/**
 * Production configuration (the default for `ng build`).
 *
 * apiBaseUrl is prepended to the backend's /api/... paths. The frontend is a
 * Render Static Site without rewrites, so production calls the backend
 * directly; the backend must allow this site's origin in CORS_ORIGINS.
 */
export const environment = {
  apiBaseUrl: 'https://nutrition-assistant-api.onrender.com',
};
