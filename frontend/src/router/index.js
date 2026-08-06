// src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import { useUserStore } from '@/stores/user';

// Import the new shell component
import AppShell from '@/components/AppShell.vue';

const PlanView = () => import('@/views/PlanView.vue');
const NavView  = () => import('@/views/NavView.vue');
const LoginView = () => import('@/views/LoginView.vue');
const ChatView = () => import('@/views/ChatView.vue');

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    {
      // New parent route for all authenticated views
      path: '/app',
      component: AppShell,
      meta: { requiresAuth: true },
      // Redirect /app to /app/chat by default
      redirect: { name: 'chat' }, 
      children: [
        {
          path: 'chat',
          name: 'chat',
          component: ChatView,
        },
        // Future authenticated views like a settings page can be added here
      ]
    },
    // Legacy routes, kept for now
    { path: '/plan', name: 'plan', component: PlanView },
    { path: '/nav',  name: 'nav',  component: NavView  },
    // Redirect root to login, the guard will handle redirecting to chat if already logged in
    { path: '/', redirect: '/login' },
  ],
});

router.beforeEach((to, from, next) => {
  const user = sessionStorage.getItem('user');
  const isLoggedIn = !!user;

  if (to.matched.some(record => record.meta.requiresAuth)) {
    if (!isLoggedIn) {
      next({ name: 'login' });
    } else {
      next();
    }
  } else if (to.name === 'login' && isLoggedIn) {
    // If a logged-in user tries to access the login page, redirect them to the chat
    next({ name: 'chat' });
  } else {
    next();
  }
});

export default router;