import i18next from 'i18next';

const resources = {
  en: {
    translation: require('./en.json')
  },
  'zh-CN': {
    translation: require('./zh-CN.json')
  }
};

i18next.init({
  lng: 'en',
  fallbackLng: 'en',
  resources,
});

export default i18next;
