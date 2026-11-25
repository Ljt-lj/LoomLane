import { Braces, Code2, Plus, Trash2 } from 'lucide-react'
import type { TransformExpression, TransformField } from './types'
import { buildTransformExpression, transformMode } from './transform-utils'

interface TransformEditorProps {
  title: string
  value: TransformExpression | null | undefined
  defaultValue: TransformExpression
  defaultAdvancedValue: TransformExpression
  defaultField: TransformField
  placeholder: string
  hint?: string
  showToggle?: boolean
  onChange: (value: TransformExpression | null) => void
}

export default function TransformEditor({
  title,
  value,
  defaultValue,
  defaultAdvancedValue,
  defaultField,
  placeholder,
  hint,
  showToggle = true,
  onChange,
}: TransformEditorProps) {
  const active = Boolean(value)
  const mode = value ? transformMode(value) : 'fields'
  const fields = value?.fields ?? []

  function setFields(nextFields: TransformField[]) {
    if (!nextFields.length) {
      onChange(null)
      return
    }
    onChange({
      mode: 'fields',
      fields: nextFields,
    })
  }

  function updateField(
    index: number,
    attribute: keyof TransformField,
    nextValue: string,
  ) {
    setFields(
      fields.map((field, fieldIndex) =>
        fieldIndex === index
          ? { ...field, [attribute]: nextValue }
          : field,
      ),
    )
  }

  if (!showToggle && !active) {
    return null
  }

  return (
    <section className="transform-editor">
      <div className="section-line">
        <span className="transform-editor-title">
          <Braces size={13} />
          {title}
          {active && (
            <span className="transform-mode-chip">
              {mode === 'fields' ? '字段' : '表达式'}
            </span>
          )}
        </span>
        <span className="transform-editor-actions">
          {active && mode === 'fields' && (
            <button
              type="button"
              className="icon-button"
              title="转换为高级表达式"
              aria-label="转换为高级表达式"
              onClick={() =>
                onChange({
                  mode: 'expression',
                  expression: buildTransformExpression(fields),
                })
              }
            >
              <Code2 size={13} />
            </button>
          )}
          {showToggle && active && (
            <button
              type="button"
              className="icon-button danger"
              title={`清除${title}`}
              aria-label={`清除${title}`}
              onClick={() => onChange(null)}
            >
              <Trash2 size={13} />
            </button>
          )}
          {showToggle && !active && (
            <>
              <button
                type="button"
                className="small-button"
                onClick={() =>
                  onChange({
                    ...defaultValue,
                    fields: defaultValue.fields?.map((field) => ({
                      ...field,
                    })),
                  })
                }
              >
                <Plus size={12} />
                添加字段
              </button>
              <button
                type="button"
                className="icon-button"
                title="添加高级表达式"
                aria-label="添加高级表达式"
                onClick={() =>
                  onChange({
                    ...defaultAdvancedValue,
                  })
                }
              >
                <Code2 size={13} />
              </button>
            </>
          )}
        </span>
      </div>
      {active && (
        <>
          {mode === 'fields' ? (
            <div className="transform-fields">
              <div className="transform-field-heading">
                <span>输出路径</span>
                <span>值表达式</span>
                <span />
              </div>
              {fields.map((field, index) => (
                <div className="transform-field-row" key={index}>
                  <input
                    value={field.path}
                    placeholder="brief"
                    aria-label={`${title}第 ${index + 1} 项输出路径`}
                    onChange={(event) =>
                      updateField(index, 'path', event.target.value)
                    }
                  />
                  <input
                    value={field.value}
                    spellCheck={false}
                    placeholder={placeholder}
                    aria-label={`${title}第 ${index + 1} 项值`}
                    onChange={(event) =>
                      updateField(index, 'value', event.target.value)
                    }
                  />
                  <button
                    type="button"
                    className="icon-button danger"
                    title="删除字段"
                    aria-label="删除字段"
                    onClick={() =>
                      setFields(
                        fields.filter(
                          (_, fieldIndex) => fieldIndex !== index,
                        ),
                      )
                    }
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="small-button transform-add-field"
                onClick={() =>
                  setFields([
                    ...fields,
                    {
                      ...defaultField,
                    },
                  ])
                }
              >
                <Plus size={12} />
                添加字段
              </button>
            </div>
          ) : (
            <textarea
              rows={4}
              spellCheck={false}
              value={value?.expression ?? ''}
              placeholder={placeholder}
              aria-label={`${title}表达式`}
              onChange={(event) =>
                onChange({
                  mode: 'expression',
                  expression: event.target.value,
                })
              }
            />
          )}
          {hint && <div className="transform-editor-hint">{hint}</div>}
        </>
      )}
    </section>
  )
}
