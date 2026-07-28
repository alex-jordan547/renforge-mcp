import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';

// Clé de stockage pour persistance de la langue choisie par l'utilisateur
const LANG_STORAGE_KEY = 'renforge-lang';

/** Détecte la langue persistée ou utilise le navigateur en fallback vers 'en'. */
function detectLanguage(): string {
  const stored = localStorage.getItem(LANG_STORAGE_KEY);
  if (stored === 'zh-CN' || stored === 'en') return stored;
  const browser = navigator.language.toLowerCase();
  if (browser.startsWith('zh')) return 'zh-CN';
  return 'en';
}

export const resources = {
  en: { translation: en },
  'zh-CN': { translation: zhCN },
} as const;

i18next
  .use(initReactI18next)
  .init({
    lng: detectLanguage(),
    fallbackLng: 'en',
    resources,
    interpolation: {
      // React se charge déjà de l'échappement XSS
      escapeValue: false,
    },
  });

// Synchronise document.documentElement.lang à chaque changement de langue
i18next.on('languageChanged', (lng: string) => {
  document.documentElement.lang = lng;
});

// Applique la langue initiale dès le chargement
document.documentElement.lang = i18next.language;

/** Change la langue active et la persiste dans localStorage. */
export function setLanguage(lang: 'en' | 'zh-CN'): void {
  localStorage.setItem(LANG_STORAGE_KEY, lang);
  void i18next.changeLanguage(lang);
}

export default i18next;
