---
title: NirnAIn Project Planning Demo
emoji: 📋
colorFrom: green
colorTo: yellow
sdk: static
app_build_command: node huggingface/build-static.mjs
app_file: dist/index.html
---

# NirnAIn — Static Hugging Face Space

This is a deployment recipe for the existing Vite frontend. Hugging Face serves the built site; the frontend calls an **existing HTTPS FastAPI service**, which connects to Supabase. The Static Space does not host FastAPI, run the optimizer, or itself connect to the database. Existing UI and behavior are unchanged. In particular, deployment does not make prototype features operational.

Static Spaces are free according to [Hugging Face's docs](https://huggingface.co/docs/hub/spaces-sdks-static). The API, database, AI providers, and any other services have their own availability, limits, and costs.

## Build and publish in a separate Space

1. Create a **Static** Space with the required visibility. A public Space makes its repository visible to everyone; verify that publishing the source is authorized. It is not necessary to modify the original GitHub repository.
2. Create a separate deployment copy containing the frontend source, `package.json`, `pnpm-lock.yaml`, Vite/TypeScript configuration, `.figma/`, `public/` (if present), and `huggingface/build-static.mjs`. Exclude `.git`, `node_modules`, `.env*`, database passwords, license files, keys and unrelated sensitive material. Copy this file to the root of the Space repository as `README.md` so Hugging Face reads its YAML metadata. Do not overwrite the original repository's README.
3. Prepare `huggingface/public-config.json` **in the deployment copy**, based on `huggingface/public-config.example.json`. The values are public and embedded in the browser bundle:

| Key | Use |
|---|---|
| `VITE_SUPABASE_URL` | Supabase project HTTPS URL |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable/anon key only |
| `VITE_API_URL` | Existing FastAPI HTTPS origin with no trailing slash, e.g. `https://api.example.com` |

The build script also accepts these public values as build-job environment variables, taking precedence over the JSON file. Static Space documentation describes values available to browser JavaScript; **do not assume that Space settings variables are injected into a separate build job without verifying**. The checked-in public-config JSON offers an explicit fallback. Change the demo key and URLs only in the deployment copy. The API must use the same Supabase project as the browser.

**Never put service-role/secret keys, database passwords, JWT signing secrets, AI provider keys or Gurobi credentials in a Static Space, a `VITE_*` variable, public-config JSON, or frontend source.** Even values called secrets may be exposed to JavaScript in Static Spaces. Supabase's publishable/anon key is intended for browsers only with properly configured row-level security. Sensitive configuration must stay on the external backend.

4. On the API host, add the Space's exact browser origin (for example `https://<owner>-<space>.hf.space`) to `CORS_ORIGINS` and redeploy the backend according to its existing deployment process. Add the correct Space redirect/callback URLs to Supabase Auth and provider allowlists as needed. Prefer a separate demo backend and Supabase project; changing production configuration affects real users.
5. Push the deployment copy to the Space. The metadata asks Hugging Face to run the build command and serve `dist/index.html`. Its published build helper detects `pnpm-lock.yaml` and installs dependencies; confirm the build logs. If Node or pnpm versions differ from project requirements, adjust the build environment or build locally and publish `dist` as static assets instead. This recipe has not been deployed or tested on Hugging Face.
6. Test sign-up/login, OAuth or email links if used, and an authenticated API request. Verify the direct `.hf.space` URL as well as the embedded Space page.

## Important routing and security checks

The application uses browser-history routing. Directly open and refresh `/login`, `/dashboard`, `/auth/confirm`, and a `/projects/<id>/...` route in the deployed Space. HF's static documentation does not establish an index fallback for arbitrary paths; a client-side navigation may work while a refresh, deep link, or auth callback returns 404. This recipe does **not** assume an undocumented rewrite mechanism. If fallback is unavailable, use a host with an SPA fallback, adopt hash routing in a separately reviewed change including auth redirects, or use the optional Docker/reverse-proxy recipe in `huggingface/README-Docker.md`.

Static hosting does not use the repo's Caddy security headers or its `/api` proxy. The browser contacts the HTTPS API directly, so correct CORS is essential. Review host-provided headers and test iframe-based login. Static Spaces expose their browser variables to client-side JavaScript and should not hold confidential application secrets.

## Optional Docker alternative

`huggingface/Dockerfile`, `huggingface/Caddyfile` and `huggingface/README-Docker.md` provide a one-port Caddy frontend and same-origin reverse proxy to the external API. This can provide the SPA fallback, but CPU Basic Docker Space eligibility can require a subscription and Spaces may sleep. It still cannot use ordinary direct Supabase Postgres ports from the Space; see the Docker instructions.

The default project solver is OR-Tools CP-SAT. The separate Gurobi adapter requires the Python package, an appropriate license, explicit backend configuration, and end-to-end testing on the API host. No hosting or production change is performed by these files.

References: [Static Spaces](https://huggingface.co/docs/hub/spaces-sdks-static), [Space configuration](https://huggingface.co/docs/hub/spaces-config-reference), [React Router SPA fallback](https://reactrouter.com/how-to/spa), and [Space build helper](https://huggingface.co/spaces/huggingface/space-build/blob/main/build.sh).
