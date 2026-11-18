import { Braces, CornerDownRight, Plus, Trash2 } from 'lucide-react'
import type { AgentJsonOutput } from './types'

type JsonObject = Record<string, unknown>

interface JsonFieldListProps {
  value: JsonObject
  path: string
  depth: number
  onChange: (value: JsonObject) => void
}

interface JsonOutputEditorProps {
  value: AgentJsonOutput
  outputNames: string[]
  onChange: (value: AgentJsonOutput) => void
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function uniqueFieldName(keys: string[]) {
  const taken = new Set(keys)
  const base = 'field'
  if (!taken.has(base)) return base
  let index = 2
  while (taken.has(`${base}-${index}`)) index += 1
  return `${base}-${index}`
}

function formatLeaf(value: unknown) {
  if (typeof value === 'string') return value
  const rendered = JSON.stringify(value)
  return rendered ?? ''
}

function parseLeaf(text: string): unknown {
  if (text === '') return ''
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function JsonFieldList({
  value,
  path,
  depth,
  onChange,
}: JsonFieldListProps) {
  const entries = Object.entries(value)

  function addField() {
    const key = uniqueFieldName(Object.keys(value))
    onChange({ ...value, [key]: '' })
  }

  function renameField(previousKey: string, nextKey: string) {
    if (previousKey === nextKey) return
    if (Object.prototype.hasOwnProperty.call(value, nextKey)) return
    const renamed: JsonObject = {}
    for (const [key, child] of entries) {
      renamed[key === previousKey ? nextKey : key] = child
    }
    onChange(renamed)
  }

  function updateField(key: string, nextValue: unknown) {
    onChange({ ...value, [key]: nextValue })
  }

  function removeField(key: string) {
    const next = { ...value }
    delete next[key]
    onChange(next)
  }

  function addChild(key: string) {
    const current = value[key]
    updateField(
      key,
      isJsonObject(current)
        ? {
            ...current,
            [uniqueFieldName(Object.keys(current))]: '',
          }
        : { field: '' },
    )
  }

  return (
    <div
      className={`json-field-list${depth > 0 ? ' is-nested' : ''}`}
      data-json-path={path}
    >
      {entries.map(([key, child], index) => {
        const objectValue = isJsonObject(child)
        return (
          <div className="json-field-entry" key={index}>
            <div className={`json-field-row${objectValue ? ' is-object' : ''}`}>
              <input
                aria-label={`${path}.${key} 键名`}
                className="json-field-key"
                value={key}
                placeholder="字段"
                onChange={(event) =>
                  renameField(key, event.target.value.trimStart())
                }
              />
              {objectValue ? (
                <span className="json-field-object-label">对象</span>
              ) : (
                <input
                  aria-label={`${path}.${key} 值`}
                  className="json-field-value"
                  value={formatLeaf(child)}
                  placeholder="值或 JSON"
                  onChange={(event) =>
                    updateField(key, parseLeaf(event.target.value))
                  }
                />
              )}
              <button
                type="button"
                className="icon-button"
                title="添加子字段"
                onClick={() => addChild(key)}
              >
                <CornerDownRight size={13} />
              </button>
              <button
                type="button"
                className="icon-button danger"
                title="删除字段"
                onClick={() => removeField(key)}
              >
                <Trash2 size={13} />
              </button>
            </div>
            {objectValue && (
              <JsonFieldList
                value={child}
                path={`${path}.${key}`}
                depth={depth + 1}
                onChange={(nextValue) => updateField(key, nextValue)}
              />
            )}
          </div>
        )
      })}
      <button
        type="button"
        className="json-add-field"
        onClick={addField}
      >
        <Plus size={12} />
        添加字段
      </button>
    </div>
  )
}

export default function JsonOutputEditor({
  value,
  outputNames,
  onChange,
}: JsonOutputEditorProps) {
  function toggleEnabled(enabled: boolean) {
    const template =
      enabled && Object.keys(value.template).length === 0
        ? Object.fromEntries(outputNames.map((name) => [name, '']))
        : value.template
    onChange({ enabled, template })
  }

  return (
    <section className="json-output-section">
      <div className="json-output-heading">
        <span className="json-output-title">
          <Braces size={14} />
          JSON 输出
        </span>
        <label className="json-output-toggle">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(event) => toggleEnabled(event.target.checked)}
          />
          启用
        </label>
      </div>
      {value.enabled && (
        <>
          <JsonFieldList
            value={value.template}
            path="output"
            depth={0}
            onChange={(template) => onChange({ ...value, template })}
          />
          <details className="json-output-preview">
            <summary>JSON 预览</summary>
            <pre>{JSON.stringify(value.template, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  )
}
