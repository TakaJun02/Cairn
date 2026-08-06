// src/main.js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'
import { useNavStore } from '@/stores/nav'
import router from './router'
import App from './App.vue'
import './assets/design-system.css'

const app = createApp(App)
const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

app.use(pinia)

async function bootstrap() {
  const storedUser = JSON.parse(sessionStorage.getItem('user') || 'null')
  if (storedUser?.token) {
    try {
      await useNavStore(pinia).fetchSpots()
    } catch (error) {
      console.error('Failed to preload spots:', error)
    }
  }

  app.use(router)
  app.mount('#app')
}

void bootstrap()

// Service Worker（public/sw.js がある前提）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
