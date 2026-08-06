export async function sendSwMessage(payload) {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return false
  }

  const postTo = (controller) => {
    if (!controller) return false
    try {
      controller.postMessage(payload)
      return true
    } catch (err) {
      console.warn('[sw] failed to post message', payload?.type, err)
      return false
    }
  }

  if (postTo(navigator.serviceWorker.controller)) {
    return true
  }

  try {
    const registration = await navigator.serviceWorker.ready
    return postTo(registration.active)
  } catch (err) {
    console.warn('[sw] service worker ready wait failed', err)
    return false
  }
}
