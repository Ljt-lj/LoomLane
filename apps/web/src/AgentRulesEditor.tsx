import { ListChecks, Plus, Trash2 } from 'lucide-react'
import { useRef } from 'react'
import type { AgentRule, AgentRulePhase } from './types'

interface AgentRulesEditorProps {
  value: AgentRule[]
  onChange: (value: AgentRule[]) => void
}

function nextRule(): AgentRule {
  return {
    phase: 'output',
    expression: 'True',
    message: '',
    enabled: true,
  }
}

export default function AgentRulesEditor({
  value,
  onChange,
}: AgentRulesEditorProps) {
  const nextKey = useRef(value.length)
  const keys = useRef(value.map((_, index) => `rule-${index}`))
  while (keys.current.length < value.length) {
    keys.current.push(`rule-${nextKey.current++}`)
  }
  if (keys.current.length > value.length) {
    keys.current = keys.current.slice(0, value.length)
  }

  function updateRule(index: number, patch: Partial<AgentRule>) {
    onChange(
      value.map((rule, ruleIndex) =>
        ruleIndex === index ? { ...rule, ...patch } : rule,
      ),
    )
  }

  function addRule() {
    const rule = nextRule()
    keys.current = [...keys.current, `rule-${nextKey.current++}`]
    onChange([...value, rule])
  }

  function removeRule(index: number) {
    keys.current = keys.current.filter((_, keyIndex) => keyIndex !== index)
    onChange(value.filter((_, ruleIndex) => ruleIndex !== index))
  }

  return (
    <section className="agent-rules-section">
      <div className="section-line">
        <span className="agent-rules-title">
          <ListChecks size={13} />
          Python 规则
        </span>
        <button type="button" className="small-button" onClick={addRule}>
          <Plus size={12} />
          规则
        </button>
      </div>
      {value.length === 0 ? (
        <button
          type="button"
          className="agent-rules-empty"
          onClick={addRule}
        >
          <Plus size={12} />
          添加第一条规则
        </button>
      ) : (
        <div className="agent-rule-list">
          {value.map((rule, index) => (
            <div className="agent-rule-card" key={keys.current[index]}>
              <div className="agent-rule-header">
                <label className="agent-rule-enabled">
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(event) =>
                      updateRule(index, { enabled: event.target.checked })
                    }
                  />
                  启用
                </label>
                <select
                  aria-label="规则阶段"
                  value={rule.phase}
                  onChange={(event) =>
                    updateRule(index, {
                      phase: event.target.value as AgentRulePhase,
                    })
                  }
                >
                  <option value="input">输入前</option>
                  <option value="output">输出后</option>
                </select>
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label="删除规则"
                  title="删除规则"
                  onClick={() => removeRule(index)}
                >
                  <Trash2 size={13} />
                </button>
              </div>
              <label>
                表达式
                <textarea
                  rows={3}
                  spellCheck={false}
                  value={rule.expression}
                  placeholder={
                    rule.phase === 'input'
                      ? 'len(inputs.brief) <= 5000'
                      : 'not_empty(outputs.draft)'
                  }
                  onChange={(event) =>
                    updateRule(index, { expression: event.target.value })
                  }
                />
              </label>
              <label>
                失败提示
                <input
                  value={rule.message ?? ''}
                  placeholder="可选"
                  onChange={(event) =>
                    updateRule(index, {
                      message: event.target.value || null,
                    })
                  }
                />
              </label>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
