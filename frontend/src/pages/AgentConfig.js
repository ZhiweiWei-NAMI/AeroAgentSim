import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Button, Modal, Form, Input, Select, 
  InputNumber, Space, Spin, Alert, Popconfirm, Collapse, 
  Tooltip, Tag, Checkbox, Row, Col
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, InfoCircleOutlined,
  RobotOutlined, ThunderboltOutlined, ReloadOutlined
} from '@ant-design/icons';
import { agentApi } from '../services/api';

const { Option } = Select;
const { Panel } = Collapse;

const AgentConfig = () => {
  const [form] = Form.useForm();
  const [agents, setAgents] = useState([]);
  const [agentTemplates, setAgentTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [selectedComponents, setSelectedComponents] = useState([]);
  
  // 获取数据
  const fetchData = async () => {
    setLoading(true);
    try {
      const [agentsRes, templatesRes] = await Promise.all([
        agentApi.getAllAgents(),
        agentApi.getAgentTemplates()
      ]);
      // console.log(agentsRes);
      setAgents(agentsRes);
      setAgentTemplates(templatesRes);
      setError(null);
    } catch (err) {
      console.error('获取智能体数据失败:', err);
      setError('获取智能体数据失败，请检查网络连接或服务器状态');
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchData();
  }, []);
  
  // 处理智能体模板选择
  const handleTemplateChange = (templateId) => {
    const template = agentTemplates.find(t => t.id === templateId);
    setSelectedTemplate(template);
    
    if (template) {
      setSelectedComponents(template.components || []);
      
      // 更新表单中的组件选择以及实际类型名称
      form.setFieldsValue({
        components: template.components || [],
        // 直接使用模板ID作为type
        template_type: templateId
      });
    } else {
      setSelectedComponents([]);
    }
  };
  
  // 处理组件选择变化
  const handleComponentsChange = (checkedValues) => {
    setSelectedComponents(checkedValues);
  };
  
  // 创建新智能体
  const handleCreateAgent = async (values) => {
    try {
      // 处理初始位置格式
      let initialPosition = [0, 0, 0];
      if (values.initial_position) {
        const positionStr = values.initial_position;
        if (typeof positionStr === 'string') {
            console.log(positionStr);
            initialPosition = positionStr.split(',').map(num => parseFloat(num.trim()));
        }
      }
      
      // 处理属性
      const properties = {};
      if (values.properties) {
        Object.keys(values.properties).forEach(key => {
          if (values.properties[key] !== undefined && values.properties[key] !== '') {
            properties[key] = values.properties[key];
          }
        });
      }
      
      // 使用模板的实际类型名称，而不是模板ID
      const agentData = {
        name: values.name,
        type: values.template_type || values.type, // 优先使用模板的实际类型名称
        initial_position: initialPosition,
        initial_battery: values.initial_battery || 100,
        components: values.components,
        properties: properties
      };
      
      console.log('创建智能体数据:', agentData);
      
      const result = await agentApi.createAgent(agentData);
      console.log('创建智能体结果:', result);
      
      setModalVisible(false);
      form.resetFields();
      
      // 添加延迟加载，解决后端数据同步问题
      setTimeout(async () => {
        await fetchData();
        // 如果没有获取到新的智能体，再次尝试
        setTimeout(async () => {
          await fetchData();
        }, 1000);
      }, 500);
    } catch (err) {
      console.error('创建智能体失败:', err);
      Modal.error({
        title: '创建失败',
        content: '创建智能体失败，请稍后重试'
      });
    }
  };
  
  // 删除智能体
  const handleDeleteAgent = async (agentId) => {
    try {
      await agentApi.deleteAgent(agentId);
      fetchData(); // 刷新列表
    } catch (err) {
      console.error('删除智能体失败:', err);
      Modal.error({
        title: '删除失败',
        content: '删除智能体失败，请稍后重试'
      });
    }
  };
  
  // 表格列定义
  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      render: type => {
        const icon = type.toLowerCase().includes('drone') ? <RobotOutlined /> : <ThunderboltOutlined />;
        return <Tag icon={icon}>{type}</Tag>;
      }
    },
    {
      title: '位置',
      dataIndex: 'position',
      key: 'position',
      render: position => position ? `(${position[0].toFixed(2)}, ${position[1].toFixed(2)}, ${position[2].toFixed(2)})` : '未知'
    },
    {
      title: '创建时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      render: timestamp => new Date(timestamp).toLocaleString()
    },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Space>
          <Tooltip title="查看详情">
            <Button 
              type="link" 
              icon={<InfoCircleOutlined />}
              onClick={() => showAgentDetails(record)}
            />
          </Tooltip>
          <Popconfirm
            title="确定要删除此智能体吗？"
            onConfirm={() => handleDeleteAgent(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button 
              type="link" 
              danger 
              icon={<DeleteOutlined />}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];
  
  // 显示智能体详情
  const showAgentDetails = (agent) => {
    Modal.info({
      title: `智能体详情 - ${agent.name}`,
      width: 600,
      content: (
        <div>
          <p><strong>ID:</strong> {agent.id}</p>
          <p><strong>名称:</strong> {agent.name}</p>
          <p><strong>类型:</strong> {agent.type}</p>
          <p><strong>位置:</strong> {agent.position ? `(${agent.position[0].toFixed(2)}, ${agent.position[1].toFixed(2)}, ${agent.position[2].toFixed(2)})` : '未知'}</p>
          <p><strong>创建时间:</strong> {new Date(agent.timestamp).toLocaleString()}</p>
          
          <Collapse>
            <Panel header="组件和属性" key="1">
              <div>
                <h4>组件</h4>
                <div>
                  {agent.properties && agent.properties.components ? 
                    agent.properties.components.map(comp => (
                      <Tag key={comp} color="blue">{comp}</Tag>
                    ))
                    : '无组件信息'
                  }
                </div>
                
                <h4>属性</h4>
                <pre>{JSON.stringify(agent.properties, null, 2)}</pre>
              </div>
            </Panel>
          </Collapse>
        </div>
      ),
    });
  };
  
  // 渲染组件选择
  const renderComponentsSelection = () => {
    if (!selectedTemplate) return null;
    
    const allComponents = [
      'MoveTo', 'Charging', 'Compute', 'Sensing', 
      'Communication', 'Storage', 'Inspection'
    ];
    
    return (
      <Form.Item
        name="components"
        label="组件"
        rules={[{ required: true, message: '请选择至少一个组件' }]}
      >
        <Checkbox.Group onChange={handleComponentsChange}>
          <Row>
            {allComponents.map(component => (
              <Col span={8} key={component}>
                <Checkbox 
                  value={component}
                  disabled={selectedTemplate.components && 
                    !selectedTemplate.components.includes(component)}
                >
                  {component}
                </Checkbox>
              </Col>
            ))}
          </Row>
        </Checkbox.Group>
      </Form.Item>
    );
  };
  
  // 渲染属性字段
  const renderPropertyFields = () => {
    if (!selectedTemplate) return null;
    
    // 根据选择的模板和组件渲染不同的属性字段
    const propertyFields = [];
    
    // 基本属性
    propertyFields.push(
      <Form.Item
        key="initial_position"
        name="initial_position"
        label="初始位置"
        rules={[{ required: true, message: '请输入初始位置' }]}
      >
        <Input placeholder="格式: x,y,z 例如: 10,20,30" />
      </Form.Item>
    );
    
    propertyFields.push(
      <Form.Item
        key="initial_battery"
        name="initial_battery"
        label="初始电量"
        initialValue={100}
      >
        <InputNumber min={0} max={100} formatter={value => `${value}%`} />
      </Form.Item>
    );
    
    // 根据选择的组件添加特定属性
    if (selectedComponents.includes('MoveTo')) {
      propertyFields.push(
        <Form.Item
          key="max_speed"
          name={['properties', 'max_speed']}
          label="最大速度 (m/s)"
        >
          <InputNumber min={0} step={0.1} />
        </Form.Item>
      );
    }
    
    if (selectedComponents.includes('Charging')) {
      propertyFields.push(
        <Form.Item
          key="charging_rate"
          name={['properties', 'charging_rate']}
          label="充电速率 (%/min)"
        >
          <InputNumber min={0} step={0.1} />
        </Form.Item>
      );
    }
    
    if (selectedComponents.includes('Compute')) {
      propertyFields.push(
        <Form.Item
          key="compute_power"
          name={['properties', 'compute_power']}
          label="计算能力"
        >
          <InputNumber min={0} />
        </Form.Item>
      );
    }
    
    return propertyFields;
  };
  
  return (
    <div className="agent-config-container">
      <h2>智能体配置</h2>
      
      {error && <Alert message={error} type="error" showIcon style={{ marginBottom: 16 }} />}
      
      <Spin spinning={loading}>
        <Card
          title="智能体列表"
          extra={
            <Space>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setModalVisible(true)}
              >
                创建智能体
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={fetchData}
              >
                刷新列表
              </Button>
            </Space>
          }
        >
          <Table 
            dataSource={agents} 
            columns={columns} 
            rowKey="id"
            pagination={{ pageSize: 10 }}
          />
        </Card>
      </Spin>
      
      {/* 创建智能体模态框 */}
      <Modal
        title="创建新智能体"
        visible={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields(); // 重置所有字段，包括隐藏的template_type
          setSelectedTemplate(null);
          setSelectedComponents([]);
        }}
        footer={null}
        width={700}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreateAgent}
        >
          {/* 隐藏字段：存储模板的实际类型名称 */}
          <Form.Item name="template_type" hidden>
            <Input />
          </Form.Item>
          
          <Form.Item
            name="name"
            label="智能体名称"
            rules={[{ required: true, message: '请输入智能体名称' }]}
          >
            <Input placeholder="请输入智能体名称" />
          </Form.Item>
          
          <Form.Item
            name="type"
            label="智能体类型"
            rules={[{ required: true, message: '请选择智能体类型' }]}
          >
            <Select 
              placeholder="选择智能体类型" 
              onChange={handleTemplateChange}
            >
              {(Array.isArray(agentTemplates) ? agentTemplates : []).map(template => (
                <Option key={template.id} value={template.id}>
                  {template.name} - {template.description}
                </Option>
              ))}
            </Select>
          </Form.Item>
          
          {renderComponentsSelection()}
          
          {renderPropertyFields()}
          
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                创建
              </Button>
              <Button onClick={() => {
                setModalVisible(false);
                form.resetFields();
                setSelectedTemplate(null);
                setSelectedComponents([]);
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default AgentConfig;