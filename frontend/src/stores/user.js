// frontend/src/stores/user.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginUser, createUser as apiCreateUser } from '@/lib/api' // `createUser` が衝突するのでエイリアス
import router from '@/router' // Import router instance
import { useNavStore } from '@/stores/nav'

export const useUserStore = defineStore('user', () => {
  // State
  const user = ref(JSON.parse(sessionStorage.getItem('user') || 'null'))
  const isLoggedIn = computed(() => !!user.value)
  const userName = computed(() => user.value?.user_name || '')
  const token = computed(() => user.value?.token || '')

  async function _loadSpots() {
    try {
      await useNavStore().fetchSpots()
    } catch (error) {
      console.error('Failed to load spots:', error)
    }
  }

  // Private helper
  function _setUser(userData) {
    user.value = userData
    if (userData) {
      sessionStorage.setItem('user', JSON.stringify(userData))
    } else {
      sessionStorage.removeItem('user')
    }
  }

  // Actions
  async function login(username) {
    try {
      const userData = await loginUser(username)
      _setUser(userData)
      await _loadSpots()
      return { success: true }
    } catch (error) {
      console.error('Login failed:', error)
      _setUser(null)
      return { success: false, error }
    }
  }

  async function register(username, language = 'ja') {
    try {
      // 登録成功後、そのままログイン状態とする
      const userData = await apiCreateUser(username)
      _setUser({ ...userData, language })
      await _loadSpots()
      return { success: true }
    } catch (error) {
      console.error('Registration failed:', error)
      _setUser(null)
      return { success: false, error }
    }
  }

  function logout() {
    _setUser(null)
    router.push('/login')
  }

  return {
    user,
    isLoggedIn,
    userName,
    token,
    login,
    register,
    logout,
  }
})
