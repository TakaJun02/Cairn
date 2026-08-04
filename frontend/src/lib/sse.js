export function createSseParser(onEvent) {
  let buffer = ''

  const dispatch = (block) => {
    let event = 'message'
    const dataLines = []

    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator === -1 ? line : line.slice(0, separator)
      let value = separator === -1 ? '' : line.slice(separator + 1)
      if (value.startsWith(' ')) value = value.slice(1)

      if (field === 'event') event = value
      if (field === 'data') dataLines.push(value)
    }

    if (dataLines.length === 0) return
    const rawData = dataLines.join('\n')
    onEvent({ event, data: JSON.parse(rawData) })
  }

  const push = (chunk) => {
    buffer += chunk
    if (buffer.charCodeAt(0) === 0xfeff) buffer = buffer.slice(1)

    let boundary = buffer.match(/\r?\n\r?\n/)
    while (boundary) {
      const block = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      dispatch(block)
      boundary = buffer.match(/\r?\n\r?\n/)
    }
  }

  const flush = () => {
    if (buffer.trim()) dispatch(buffer)
    buffer = ''
  }

  return { push, flush }
}

export function parseSseText(text) {
  const events = []
  const parser = createSseParser((event) => events.push(event))
  parser.push(text)
  parser.flush()
  return events
}
