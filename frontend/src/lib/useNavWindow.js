// frontend/src/lib/useNavWindow.js
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useNavStore } from '@/stores/nav'

function clamp(value, min, max) {
  if (Number.isNaN(value)) return min
  return Math.min(Math.max(value, min), max)
}

export function useNavWindow() {
  const navStore = useNavStore()
  const { plan } = storeToRefs(navStore)
  const hasRoute = computed(() => !!plan.value?.route)

  const isNavWindowVisible = ref(false)
  const isNavWindowFullScreen = ref(false)

  const MIN_NAV_WIDTH = 320
  const MIN_NAV_HEIGHT = 320

  const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : 1280
  const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 720

  const defaultNavWidth = clamp(
    viewportWidth > MIN_NAV_WIDTH + 48 ? Math.min(420, viewportWidth - 48) : MIN_NAV_WIDTH,
    MIN_NAV_WIDTH,
    Math.max(MIN_NAV_WIDTH, viewportWidth - 32)
  )
  const defaultNavHeight = clamp(
    viewportHeight > MIN_NAV_HEIGHT + 96 ? Math.min(640, viewportHeight - 80) : MIN_NAV_HEIGHT,
    MIN_NAV_HEIGHT,
    Math.max(MIN_NAV_HEIGHT, viewportHeight - 32)
  )

  const navWindow = reactive({
    x: clamp(
      Math.round((viewportWidth - defaultNavWidth) / 2),
      16,
      Math.max(16, viewportWidth - defaultNavWidth - 16)
    ),
    y: clamp(
      Math.round((viewportHeight - defaultNavHeight) / 2),
      16,
      Math.max(16, viewportHeight - defaultNavHeight - 16)
    ),
    width: defaultNavWidth,
    height: defaultNavHeight,
    dragging: false,
    dragOffsetX: 0,
    dragOffsetY: 0,
  })

  const navWindowStyle = computed(() => {
    if (isNavWindowFullScreen.value) {
      return {
        left: '0',
        top: '0',
        width: '100vw',
        height: '100vh',
        transform: 'translate3d(0, 0, 0)',
        opacity: '1',
        '--nav-window-offset': '0'
      }
    }

    const hiddenOffsetPx = (() => {
      const baseOffset = navWindow.width + 56
      if (typeof window === 'undefined') return `${baseOffset}px`
      const viewportWidth = window.innerWidth
      const distanceToViewportEdge = Math.max(0, viewportWidth - navWindow.x)
      return `${Math.max(baseOffset, distanceToViewportEdge + 40)}px`
    })()

    return {
      left: `${navWindow.x}px`,
      top: `${navWindow.y}px`,
      width: `${navWindow.width}px`,
      height: `${navWindow.height}px`,
      transform: `translate3d(var(--nav-window-offset, 0), 0, 0)`,
      '--nav-window-offset': isNavWindowVisible.value ? '0' : hiddenOffsetPx,
      opacity: isNavWindowVisible.value ? '1' : '0'
    }
  })

  let previousUserSelect = ''
  const previousWindowState = reactive({ x: navWindow.x, y: navWindow.y, width: navWindow.width, height: navWindow.height })

  function ensureNavWindowBounds() {
    if (typeof window === 'undefined' || isNavWindowFullScreen.value) return
    navWindow.width = clamp(navWindow.width, MIN_NAV_WIDTH, Math.max(MIN_NAV_WIDTH, window.innerWidth - 32))
    navWindow.height = clamp(navWindow.height, MIN_NAV_HEIGHT, Math.max(MIN_NAV_HEIGHT, window.innerHeight - 32))
    navWindow.x = clamp(navWindow.x, 16, Math.max(16, window.innerWidth - navWindow.width - 16))
    navWindow.y = clamp(navWindow.y, 16, Math.max(16, window.innerHeight - navWindow.height - 16))
  }

  function handleWindowResize() {
    ensureNavWindowBounds()
  }

  function lockUserSelect() {
    if (typeof document === 'undefined') return
    previousUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
  }

  function unlockUserSelect() {
    if (typeof document === 'undefined') return
    document.body.style.userSelect = previousUserSelect || ''
  }

  function endPointerInteraction() {
    if (navWindow.dragging) {
      navWindow.dragging = false
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', endPointerInteraction)
    }
    unlockUserSelect()
  }

  function onPointerMove(event) {
    if (typeof window === 'undefined' || !navWindow.dragging) return
    event.preventDefault()
    const maxX = Math.max(16, window.innerWidth - navWindow.width - 16)
    const maxY = Math.max(16, window.innerHeight - navWindow.height - 16)
    navWindow.x = clamp(event.clientX - navWindow.dragOffsetX, 16, maxX)
    navWindow.y = clamp(event.clientY - navWindow.dragOffsetY, 16, maxY)
  }

  function attachGlobalPointerListeners() {
    if (typeof window === 'undefined') return
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', endPointerInteraction)
  }

  function startDrag(event) {
    if (!isNavWindowVisible.value || isNavWindowFullScreen.value || typeof window === 'undefined') return
    event.preventDefault()
    event.stopPropagation()
    navWindow.dragging = true
    navWindow.dragOffsetX = event.clientX - navWindow.x
    navWindow.dragOffsetY = event.clientY - navWindow.y
    lockUserSelect()
    event.currentTarget?.setPointerCapture?.(event.pointerId)
    attachGlobalPointerListeners()
  }

  function toggleNavWindow() {
    if (!hasRoute.value) return
    isNavWindowVisible.value = !isNavWindowVisible.value
  }

  function openNavFullScreen() {
    if (!isNavWindowFullScreen.value) {
      previousWindowState.x = navWindow.x
      previousWindowState.y = navWindow.y
      previousWindowState.width = navWindow.width
      previousWindowState.height = navWindow.height
      isNavWindowFullScreen.value = true
      endPointerInteraction()
    } else {
      isNavWindowFullScreen.value = false
      navWindow.x = previousWindowState.x
      navWindow.y = previousWindowState.y
      navWindow.width = previousWindowState.width
      navWindow.height = previousWindowState.height
      ensureNavWindowBounds()
    }
  }

  onMounted(() => {
    ensureNavWindowBounds()
    if (typeof window !== 'undefined') {
      window.addEventListener('resize', handleWindowResize)
    }
  })

  onBeforeUnmount(() => {
    endPointerInteraction()
    if (typeof window !== 'undefined') {
      window.removeEventListener('resize', handleWindowResize)
    }
  })

  watch(hasRoute, (available) => {
    if (available) {
      isNavWindowFullScreen.value = false
      ensureNavWindowBounds()
    } else {
      isNavWindowVisible.value = false
      isNavWindowFullScreen.value = false
    }
  })

  watch(isNavWindowVisible, (visible) => {
    if (visible) {
      ensureNavWindowBounds()
    } else {
      endPointerInteraction()
      isNavWindowFullScreen.value = false
    }
  })

  return {
    hasRoute,
    isNavWindowVisible,
    isNavWindowFullScreen,
    navWindowStyle,
    toggleNavWindow,
    openNavFullScreen,
    startDrag,
  }
}
