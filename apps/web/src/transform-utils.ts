import type { TransformExpression, TransformField } from './types'

interface TransformTree {
  children: Map<string, TransformTree>
  value?: string
}

function emptyTransformTree(): TransformTree {
  return { children: new Map() }
}

export function transformMode(
  transform: TransformExpression,
): 'fields' | 'expression' {
  if (transform.mode === 'fields' || transform.mode === 'expression') {
    return transform.mode
  }
  return transform.fields?.length ? 'fields' : 'expression'
}

export function buildTransformExpression(
  fields: TransformField[],
): string {
  const root = emptyTransformTree()
  for (const field of fields) {
    const parts = field.path
      .split('.')
      .map((part) => part.trim())
      .filter(Boolean)
    if (!parts.length) continue
    let node = root
    for (const part of parts.slice(0, -1)) {
      let child = node.children.get(part)
      if (!child) {
        child = emptyTransformTree()
        node.children.set(part, child)
      }
      node = child
    }
    const key = parts[parts.length - 1]
    const leaf = node.children.get(key) ?? emptyTransformTree()
    leaf.value = field.value
    node.children.set(key, leaf)
  }

  function render(node: TransformTree): string {
    return `{${[...node.children.entries()]
      .map(([key, child]) => {
        const rendered =
          child.children.size === 0
            ? child.value ?? ''
            : render(child)
        return `${JSON.stringify(key)}: ${rendered}`
      })
      .join(', ')}}`
  }

  return render(root)
}
