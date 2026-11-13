---
title: NirnAIn Project Planning Demo
emoji: 📋
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
---

# NirnAIn — Hugging Face Space

This Space serves the existing Vite frontend on port 7860 and proxies `/api/*` to an existing FastAPI service over HTTPS. That backend connects to Supabase. The Space does **not** host FastAPI or connect directly to Postgres. This split is intentional: Hugging Face documents Space outbound connections on ports 80, 443 and 8080, while Supabase Postgres uses 5432 or 6543. The API host must be reachable over HTTPS and already configured with the correct Supabase project.

This deployment preserves the existing UI. It does not make unfinished features operational, set up an AI provider, configure Gurobi, or change the live deployment.

## Prepare a separate Space repository

1. Create a Docker Space with the desired visibility and choose an available hardware/billing option. Confirm permissions and costs before creation. Docker Spaces on CPU Basic may require an eligible subscription; see [Spaces documentation](https://huggingface.co/docs/huggingface_hub/guides/manage-spaces).
2. In a **separate deployment copy**, include the complete application source and lockfile from the project root, including `.figma/` and `huggingface/Caddyfile`. Do not include `.git`, `node_modules`, `dist`, `.env` files or secret files. Copy `huggingface/Dockerfile` to the Space repository root as `Dockerfile`, and copy this file to the Space repository root as `README.md`; Hugging Face reads the YAML above from the root README. Do not overwrite the original project's README or Dockerfile. The build context must be the Space repository root. Commit and push this deployment copy to the Space when you are ready.
3. Set the public **Variables** below in Space Settings **before** building. Docker build args expose these values in the frontend bundle. Rebuild whenever they change.

| Variable | Value | Visibility |
|---|---|---|
| `VITE_SUPABASE_URL` | Supabase project HTTPS URL, e.g. `https://<project-ref>.supabase.co` | Public |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable/anon key only | Public |
| `VITE_API_URL` | Your Space's exact HTTPS origin, e.g. `https://<owner>-<space>.hf.space` without a trailing slash or path | Public |
| `API_UPSTREAM` | Existing FastAPI HTTPS origin, e.g. `https://api.example.com`, without a trailing slash or path | Public, runtime |

`API_UPSTREAM` is read by Caddy at startup. Set `VITE_API_URL` to the exact browser-visible Space origin so `/api/v1/...` requests return to Caddy on the same origin. If using a custom domain, build for that origin instead. The API reverse proxy preserves the full `/api/...` path. The frontend build requires absolute URLs, so a relative value such as `/` is not valid.

**Never put a Supabase service-role/secret key, database password, JWT signing key, Gurobi license, or AI provider secret in any `VITE_*` variable, Space variable used at build time, source file or Git commit.** The frontend receives only the Supabase publishable/anon key, with RLS protecting database access. Secret backend settings belong in the FastAPI host's secrets manager. Keep your API's database and JWT verification configuration on that host; the Space does not require them.

4. Add the Space's browser origin and needed authentication callback URLs in Supabase Auth's allowed redirect URLs. Verify the provider's settings for Google/Microsoft login if used. Treat email templates and custom SMTP as separate setup. Changes to your live Supabase settings affect real users; use an isolated demo project where possible.
5. Verify the Space opens at `https://<owner>-<space>.hf.space`, `/healthz` returns `ok`, auth redirects correctly, and the browser can load `/api/v1/...` through Caddy. Test a real authorized project request end to end. `/healthz` checks Caddy only, not FastAPI or the database. Hugging Face's Space page embeds the app in an iframe; this configuration permits `https://huggingface.co` as a frame ancestor. Check the standalone `.hf.space` URL as well if OAuth popups encounter browser restrictions.

## Constraints and tradeoffs

- A free/basic Space can sleep when idle; the first request can be slow and in-progress work can be interrupted. The existing backend runs optimizer jobs in process, so the API host's own availability also matters.
- A Space update rebuilds the frontend. Only public config is compiled into it. Private user and project data lives in the configured backend/Supabase project, not in the Space's ephemeral filesystem.
- The default solver is OR-Tools CP-SAT. The separate Gurobi adapter requires the Python package, explicit backend configuration, end-to-end testing, and a suitable license. An Academic WLS license is restricted to eligible academic purposes, is registered to its user, and requires internet access; consult [Gurobi's license guidance](https://support.gurobi.com/hc/en-us/articles/34672988479633-What-are-the-restrictions-on-using-an-academic-WLS-license). Do not assume that simply adding a license changes the solver.

Official references: [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker), [Space networking and lifecycle](https://huggingface.co/docs/hub/spaces-overview), [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres).
