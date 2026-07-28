import { useTranslation } from 'react-i18next';
import { setLanguage } from '../i18n';

type Lang = 'en' | 'zh-CN';

/**
 * Sélecteur de langue persistant (EN / 中文).
 * Positionné dans la barre d'outils de l'App, à droite du toggle thème.
 */
export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language as Lang;

  function toggle() {
    const next: Lang = current === 'en' ? 'zh-CN' : 'en';
    setLanguage(next);
  }

  return (
    <button
      className="lang-toggle"
      type="button"
      onClick={toggle}
      aria-label={t('lang.switchLanguage')}
      title={t('lang.switchLanguage')}
    >
      {current === 'en' ? t('lang.zhCN') : t('lang.en')}
    </button>
  );
}
