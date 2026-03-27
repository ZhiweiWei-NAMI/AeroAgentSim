import React from 'react';
import { Button, Input, Select, Space, Switch, Table } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

const VALUE_TYPE_OPTIONS = [
  { value: 'any', label: 'any' },
  { value: 'string', label: 'string' },
  { value: 'str', label: 'str' },
  { value: 'int', label: 'int' },
  { value: 'float', label: 'float' },
  { value: 'bool', label: 'bool' },
  { value: 'list', label: 'list' },
  { value: 'dict', label: 'dict' },
];

function toRows(value) {
  return Object.entries(value || {}).map(([key, field]) => ({
    key,
    value_type: field?.value_type || 'any',
    required: Boolean(field?.required),
    default: field?.default ?? '',
    description: field?.description || '',
  }));
}

function fromRows(rows) {
  return rows.reduce((result, row) => {
    if (!row.key) {
      return result;
    }
    result[row.key] = {
      key: row.key,
      value_type: row.value_type || 'any',
      required: Boolean(row.required),
      default: row.default === '' ? null : row.default,
      description: row.description || '',
    };
    return result;
  }, {});
}

function TemplateTableEditor({ value = {}, onChange, addLabel = 'Add Row' }) {
  const rows = toRows(value);

  const updateRows = (nextRows) => {
    onChange?.(fromRows(nextRows));
  };

  const updateField = (index, field, nextValue) => {
    const nextRows = rows.slice();
    nextRows[index] = {
      ...nextRows[index],
      [field]: nextValue,
    };
    updateRows(nextRows);
  };

  const addRow = () => {
    updateRows([
      ...rows,
      {
        key: '',
        value_type: 'any',
        required: false,
        default: '',
        description: '',
      },
    ]);
  };

  const removeRow = (keyToDelete) => {
    updateRows(rows.filter((row, index) => `${row.key}_${index}` !== keyToDelete));
  };

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Button size="small" icon={<PlusOutlined />} onClick={addRow}>
        {addLabel}
      </Button>
      <Table
        size="small"
        pagination={false}
        rowKey={(row, index) => `${row.key}_${index}`}
        dataSource={rows}
        scroll={{ x: 820 }}
        columns={[
          {
            title: 'key',
            dataIndex: 'key',
            width: 150,
            render: (value, _row, index) => (
              <Input value={value} onChange={(event) => updateField(index, 'key', event.target.value)} />
            ),
          },
          {
            title: 'type',
            dataIndex: 'value_type',
            width: 140,
            render: (value, _row, index) => (
              <Select
                value={value}
                options={VALUE_TYPE_OPTIONS}
                style={{ width: '100%' }}
                onChange={(nextValue) => updateField(index, 'value_type', nextValue)}
              />
            ),
          },
          {
            title: 'required',
            dataIndex: 'required',
            width: 100,
            render: (value, _row, index) => (
              <Switch checked={Boolean(value)} onChange={(nextValue) => updateField(index, 'required', nextValue)} />
            ),
          },
          {
            title: 'default',
            dataIndex: 'default',
            width: 160,
            render: (value, _row, index) => (
              <Input value={value} onChange={(event) => updateField(index, 'default', event.target.value)} />
            ),
          },
          {
            title: 'description',
            dataIndex: 'description',
            render: (value, _row, index) => (
              <Input value={value} onChange={(event) => updateField(index, 'description', event.target.value)} />
            ),
          },
          {
            title: 'action',
            dataIndex: 'action',
            width: 72,
            render: (_value, row, index) => (
              <Button
                danger
                type="text"
                icon={<DeleteOutlined />}
                onClick={() => removeRow(`${row.key}_${index}`)}
              />
            ),
          },
        ]}
      />
    </Space>
  );
}

export default TemplateTableEditor;
