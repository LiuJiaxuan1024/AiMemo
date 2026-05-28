/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AIMEMO_BACKEND_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
