import { Braces, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ConditionNode } from './types'
import { appendConditionPredicate } from './workflow-utils'

const PREDICATE_OPERATORS = [
  'eq',
  'ne',
  'lt',
  'lte',
  'gt',
  'gte',
  'in',
  'contains',
  'exists',
  'between',
  'starts_with',
  'ends_with',
  'matches',
  'empty',
  'not_empty',
  'length_eq',
  'contains_all',
  'contains_any',
] as const

const WITHOUT_VALUE = new Set(['exists', 'empty', 'not_empty'])

const VALUE_PLACEHOLDERS: Record<string, string> = {
  between: '[最小值, 最大值]',
  matches: '正则表达式',
  length_eq: '长度',
  contains_all: '["值1", "值2"]',
  contains_any: '["值1", "值2"]',
  in: '["候选值"]',
}

interface ConditionPathOption {
  value: string
  label: string
}

interface ConditionEditorProps {
  value: ConditionNode
  pathOptions: ConditionPathOption[]
  createPredicate: () => ConditionNode
  onChange: (value: ConditionNode) => void
  onRemove?: () => void
}

function formatLiteral(value: unknown) {
  if (value !== null && typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value ?? '')
}

function parseLiteral(text: string) {
  const trimmed = text.trim()
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  if (trimmed === 'null') return null
  if (
    (trimmed.startsWith('[') && trimmed.endsWith(']')) ||
    (trimmed.startsWith('{') && trimmed.endsWith('}'))
  ) {
    try {
      return JSON.parse(trimmed)
    } catch {
      return text
    }
  }
  if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return Number(trimmed)
  return text
}

function LiteralValue({
  value,
  placeholder,
  onChange,
}: {
  value: unknown
  placeholder: string
  onChange: (value: unknown) => void
}) {
  const [text, setText] = useState(() => formatLiteral(value))

  useEffect(() => {
    setText(formatLiteral(value))
  }, [value])

  return (
    <input
      value={text}
      onChange={(event) => {
        const next = event.target.value
        setText(next)
        onChange(parseLiteral(next))
      }}
      placeholder={placeholder}
    />
  )
}

function RemoveConditionButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="icon-button danger condition-remove"
      aria-label="删除条件"
      title="删除条件"
      onClick={onClick}
    >
      <Trash2 size={13} />
    </button>
  )
}

function PredicateEditor({
  value,
  pathOptions,
  onChange,
  onRemove,
  onAddPredicate,
}: {
  value: ConditionNode
  pathOptions: ConditionPathOption[]
  onChange: (value: ConditionNode) => void
  onRemove?: () => void
  onAddPredicate?: () => void
}) {
  const left = value.left ?? ''
  const options =
    left && !pathOptions.some((path) => path.value === left)
      ? [{ value: left, label: left }, ...pathOptions]
      : pathOptions
  const operators = PREDICATE_OPERATORS.includes(
    value.op as (typeof PREDICATE_OPERATORS)[number],
  )
    ? [...PREDICATE_OPERATORS]
    : [value.op, ...PREDICATE_OPERATORS]
  const hasValue = !WITHOUT_VALUE.has(value.op)

  function changeOperator(operator: string) {
    const { right: preservedRight, ...rest } = value
    onChange(
      WITHOUT_VALUE.has(operator)
        ? { ...rest, op: operator }
        : { ...rest, op: operator, right: preservedRight },
    )
  }

  return (
    <div className="condition-leaf">
      <div
        className={`condition-predicate-fields${hasValue ? '' : ' without-value'}`}
      >
        <label>
          字段
          <select
            value={left}
            onChange={(event) =>
              onChange({ ...value, left: event.target.value })
            }
          >
            {options.map((path) => (
              <option key={path.value} value={path.value}>
                {path.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          运算符
          <select
            value={value.op}
            onChange={(event) => changeOperator(event.target.value)}
          >
            {operators.map((operator) => (
              <option key={operator}>{operator}</option>
            ))}
          </select>
        </label>
        {hasValue && (
          <label>
            比较值
            <LiteralValue
              value={value.right}
              placeholder={VALUE_PLACEHOLDERS[value.op] ?? '比较值'}
              onChange={(right) => onChange({ ...value, right })}
            />
          </label>
        )}
        {onRemove && <RemoveConditionButton onClick={onRemove} />}
      </div>
      {onAddPredicate && (
        <button
          type="button"
          className="condition-add-predicate"
          onClick={onAddPredicate}
        >
          <Plus size={12} />
          添加谓词
        </button>
      )}
    </div>
  )
}

function ConditionEditor({
  value,
  pathOptions,
  createPredicate,
  onChange,
  onRemove,
}: ConditionEditorProps) {
  if (value.op === 'and' || value.op === 'or') {
    const args = value.args ?? []
    const updateArgs = (nextArgs: ConditionNode[]) =>
      onChange({ op: value.op, args: nextArgs })

    return (
      <div className="condition-group">
        <div className="condition-group-header">
          <label className="condition-logic-field">
            组合方式
            <select
              value={value.op}
              onChange={(event) =>
                onChange({ op: event.target.value, args })
              }
            >
              <option value="and">全部满足 (AND)</option>
              <option value="or">任一满足 (OR)</option>
            </select>
          </label>
          <div className="condition-group-actions">
            <button
              type="button"
              className="small-button"
              onClick={() =>
                updateArgs([...args, createPredicate()])
              }
            >
              <Plus size={12} />
              谓词
            </button>
            <button
              type="button"
              className="small-button"
              onClick={() =>
                updateArgs([
                  ...args,
                  { op: 'and', args: [createPredicate()] },
                ])
              }
            >
              <Braces size={12} />
              条件组
            </button>
            {onRemove && <RemoveConditionButton onClick={onRemove} />}
          </div>
        </div>
        <div className="condition-group-body">
          {args.map((child, index) => (
            <ConditionEditor
              key={index}
              value={child}
              pathOptions={pathOptions}
              createPredicate={createPredicate}
              onChange={(nextChild) =>
                updateArgs(
                  args.map((item, itemIndex) =>
                    itemIndex === index ? nextChild : item,
                  ),
                )
              }
              onRemove={
                args.length > 1
                  ? () =>
                      updateArgs(
                        args.filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      )
                  : undefined
              }
            />
          ))}
          {args.length === 0 && (
            <button
              type="button"
              className="condition-empty-action"
              onClick={() => updateArgs([createPredicate()])}
            >
              <Plus size={12} />
              添加第一个谓词
            </button>
          )}
        </div>
      </div>
    )
  }

  if (value.op === 'not') {
    const child = value.args?.[0]
    return (
      <div className="condition-not">
        <div className="condition-not-header">
          <span>非 (NOT)</span>
          <div className="condition-group-actions">
            {child && (
              <button
                type="button"
                className="small-button"
                onClick={() => onChange(child)}
              >
                移除非
              </button>
            )}
            {onRemove && <RemoveConditionButton onClick={onRemove} />}
          </div>
        </div>
        {child ? (
          <ConditionEditor
            value={child}
            pathOptions={pathOptions}
            createPredicate={createPredicate}
            onChange={(nextChild) =>
              onChange({ op: 'not', args: [nextChild] })
            }
          />
        ) : (
          <button
            type="button"
            className="condition-empty-action"
            onClick={() =>
              onChange({ op: 'not', args: [createPredicate()] })
            }
          >
            <Plus size={12} />
            添加谓词
          </button>
        )}
      </div>
    )
  }

  return (
    <PredicateEditor
      value={value}
      pathOptions={pathOptions}
      onChange={onChange}
      onRemove={onRemove}
      onAddPredicate={
        onRemove
          ? undefined
          : () =>
              onChange(
                appendConditionPredicate(value, createPredicate()),
              )
      }
    />
  )
}

export default ConditionEditor
