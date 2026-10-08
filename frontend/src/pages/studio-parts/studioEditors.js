// JSON/template property editors for the workflow studio inspectors.
import React, { useEffect, useState } from 'react';
import { Input, InputNumber, Switch, Typography } from 'antd';

import { defaultValueForTemplate, isBoolean, isNumeric, isStructured } from './studioModel';

const { Text } = Typography;
const { TextArea } = Input;

function JsonField({ value, onChange }) {
  const [text, setText] = useState(JSON.stringify(value ?? {}, null, 2));
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2));
    setInvalid(false);
  }, [value]);

  return (
    <>
      <TextArea
        rows={4}
        value={text}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          if (!nextText.trim()) {
            setInvalid(false);
            onChange({});
            return;
          }
          try {
            const parsed = JSON.parse(nextText);
            setInvalid(false);
            onChange(parsed);
          } catch (_error) {
            setInvalid(true);
          }
        }}
      />
      {invalid ? <Text type="danger" className="field-help">JSON parse error</Text> : null}
    </>
  );
}

export default function TemplateEditor({ templates, values, onChange }) {
  const entries = Object.entries(templates || {});
  if (!entries.length) {
    return <Text type="secondary">No schema fields.</Text>;
  }

  return (
    <div className="editor-grid">
      {entries.map(([key, template]) => {
        const currentValue =
          values?.[key] !== undefined ? values[key] : defaultValueForTemplate(template);
        const fullWidth = isStructured(template.value_type);
        return (
          <div
            key={key}
            className={`editor-field ${fullWidth ? 'editor-field-full' : ''}`}
          >
            <Text strong>{key}</Text>
            {template.description ? <Text className="field-help">{template.description}</Text> : null}
            {isBoolean(template.value_type) ? (
              <Switch
                checked={Boolean(currentValue)}
                onChange={(checked) => onChange(key, checked)}
              />
            ) : null}
            {isNumeric(template.value_type) ? (
              <InputNumber
                value={Number(currentValue ?? 0)}
                style={{ width: '100%' }}
                onChange={(nextValue) => onChange(key, nextValue ?? 0)}
              />
            ) : null}
            {!isBoolean(template.value_type) &&
            !isNumeric(template.value_type) &&
            !isStructured(template.value_type) ? (
              <Input
                value={currentValue ?? ''}
                onChange={(event) => onChange(key, event.target.value)}
              />
            ) : null}
            {isStructured(template.value_type) ? (
              <JsonField value={currentValue} onChange={(nextValue) => onChange(key, nextValue)} />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
