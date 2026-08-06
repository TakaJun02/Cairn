<script setup>
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useNavStore } from '@/stores/nav'

const navStore = useNavStore()

// Initialize any app-wide logic here, like the device ID.
onMounted(() => {
  navStore.initializeDeviceId()
})
</script>

<template>
  <RouterView />
</template>

<style>
/* 
  All layout-specific styles have been removed from App.vue.
  The layout is now handled by AppShell.vue for authenticated routes 
  and by individual views like LoginView.vue for others.
  Global styles are in the /src/assets folder.
*/
</style>
