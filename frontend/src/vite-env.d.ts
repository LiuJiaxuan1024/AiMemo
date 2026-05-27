/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_ENABLE_WEB_ELF?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "@shikijs/langs/*" {
  import type { LanguageRegistration } from "@shikijs/types";

  const languages: LanguageRegistration[];
  export default languages;
}

declare module "@shikijs/themes/*" {
  import type { ThemeRegistrationRaw } from "@shikijs/types";

  const theme: ThemeRegistrationRaw;
  export default theme;
}
