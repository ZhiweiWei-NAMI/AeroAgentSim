import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Button, Modal, Form, Input, Select, 
  InputNumber, Space, Tag, Spin, Alert, Popconfirm, Collapse, Tooltip
} from 'antd';
import { 
  PlusOutlined, DeleteOutlined, InfoCircleOutlined,
  CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined
} from '@ant-design/icons';
import { workflowApi, agentApi } from '../services/api';

const { Option } = Select;
const { Panel } = Collapse;

const WorkflowConfig = () => {
  const [form] = Form.useForm();
  const [workflows, setWorkflows] = useState([]);
  const [workflowTemplates, setWorkflowTemplates] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  
  // 获取数据
  const fetchData = async () => {
    setLoading(true);
    try {
      const [workflowsRes, templatesRes, agentsRes] = await Promise.all([
        workflowApi.getAllWorkflows(),
        workflowApi.getWorkflowTemplates(),
        agentApi.getAllAgents()
      ]);
      
      setWorkflows(workflowsRes);
      setWorkflowTemplates(templatesRes);
      setAgents(agentsRes);
      setError(null);
    } catch (err) {
      console.error('获取工作流数据失败:', err);
      setError('获取工作流数据失败，请检查网络连接或服务器状态');
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchData();
  }, []);
  
  // 处理工作流模板选择
  const handleTemplateChange = (templateId) => {
    const template = workflowTemplates.find(t => t.id === templateId);
    setSelectedTemplate(template);
    
    // 重置表单中的参数字段
    const currentValues = form.getFieldsValue();
    form.setFieldsValue({
      ...currentValues,
      parameters: {}
    });
  };
  
  // 创建新工作流
  const handleCreateWorkflow = async (values) => {
    try {
      await workflowApi.createWorkflow({
        name: values.name,
        type: values.type,
        parameters: values.parameters,
        agent_id: values.agent_id
      });
      
      setModalVisible(false);
      form.resetFields();
      fetchData(); // 刷新列表
    } catch (err) {
      console.error('创建工作流失败:', err);
      Modal.error({
        title: '创建失败',
        content: '创建工作流失败，请稍后重试'
      });
    }
  };
  
  // 删除工作流
  const handleDeleteWorkflow = async (workflowId) => {
    try {
      await workflowApi.deleteWorkflow(workflowId);
      fetchData(); // 刷新列表
    } catch (err) {
      console.error('删除工作流失败:', err);
      Modal.error({
        title: '删除失败',
        content: '删除工作流失败，请稍后重试'
      });
    }
  };
  
  // 工作流状态标签
  const getWorkflowStatusTag = (status) => {
    const statusMap = {
      'PENDING': { color: 'default', icon: <ClockCircleOutlined />, text: '等待中' },
      'RUNNING': { color: 'processing', icon: null, text: '运行中' },
      'COMPLETED': { color: 'success', icon: <CheckCircleOutlined />, text: '已完成' },
      'FAILED': { color: 'error', icon: <CloseCircleOutlined />, text: '失败' }
    };
    
    const statusInfo = statusMap[status] || { color: 'default', text: status };
    return (
      <Tag color={statusInfo.color} icon={statusInfo.icon}>
        {statusInfo.text}
      </Tag>
    );
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
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: status => getWorkflowStatusTag(status)
    },
    {
      title: '关联智能体',
      dataIndex: 'agent_id',
      key: 'agent_id',
      render: agentId => {
        const agent = agents.find(a => a.id === agentId);
        return agent ? `${agent.name} (${agent.id})` : agentId;
      }
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
              onClick={() => showWorkflowDetails(record)}
            />
          </Tooltip>
          <Popconfirm
            title="确定要删除此工作流吗？"
            onConfirm={() => handleDeleteWorkflow(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button 
              type="link" 
              danger 
              icon={<DeleteOutlined />}
              disabled={record.status === 'RUNNING'}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];
  
  // 显示工作流详情
  const showWorkflowDetails = (workflow) => {
    Modal.info({
      title: `工作流详情 - ${workflow.name}`,
      width: 600,
      content: (
        <div>
          <p><strong>ID:</strong> {workflow.id}</p>
          <p><strong>名称:</strong> {workflow.name}</p>
          <p><strong>类型:</strong> {workflow.type}</p>
          <p><strong>状态:</strong> {getWorkflowStatusTag(workflow.status)}</p>
          <p><strong>关联智能体:</strong> {workflow.agent_id}</p>
          <p><strong>创建时间:</strong> {new Date(workflow.timestamp).toLocaleString()}</p>
          
          <Collapse>
            <Panel header="详细参数" key="1">
              <pre>{JSON.stringify(workflow.details, null, 2)}</pre>
            </Panel>
          </Collapse>
        </div>
      ),
    });
  };
  
  // 渲染动态参数表单
  const renderParameterFields = () => {
    if (!selectedTemplate) return null;
    
    return (
      <>
        <h4>参数配置</h4>
        {selectedTemplate.parameters.map(param => (
          <Form.Item
            key={param}
            label={param}
            name={['parameters', param]}
            rules={[{ required: true, message: `请输入${param}` }]}
          >
            {param.includes('point') || param.includes('position') ? (
              <Input placeholder="格式: x,y,z 例如: 10,20,30" />
            ) : param.includes('level') || param.includes('altitude') ? (
              <InputNumber min={0} />
            ) : (
              <Input placeholder={`请输入${param}`} />
            )}
          </Form.Item>
        ))}
      </>
    );
  };
  
  return (
    <div className="workflow-config-container">
      <h2>工作流配置</h2>
      
      {error && <Alert message={error} type="error" showIcon style={{ marginBottom: 16 }} />}
      
      <Spin spinning={loading}>
        <Card 
          title="工作流列表" 
          extra={
            <Button 
              type="primary" 
              icon={<PlusOutlined />} 
              onClick={() => setModalVisible(true)}
            >
              创建工作流
            </Button>
          }
        >
          <Table 
            dataSource={workflows} 
            columns={columns} 
            rowKey="id"
            pagination={{ pageSize: 10 }}
          />
        </Card>
      </Spin>
      
      {/* 创建工作流模态框 */}
      <Modal
        title="创建新工作流"
        visible={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
          setSelectedTemplate(null);
        }}
        footer={null}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreateWorkflow}
        >
          <Form.Item
            name="name"
            label="工作流名称"
            rules={[{ required: true, message: '请输入工作流名称' }]}
          >
            <Input placeholder="请输入工作流名称" />
          </Form.Item>
          
          <Form.Item
            name="type"
            label="工作流类型"
            rules={[{ required: true, message: '请选择工作流类型' }]}
          >
            <Select 
              placeholder="选择工作流类型" 
              onChange={handleTemplateChange}
            >
              {(Array.isArray(workflowTemplates) ? workflowTemplates : []).map(template => (
                <Option key={template.id} value={template.id}>
                  {template.name} - {template.description}
                </Option>
              ))}
            </Select>
          </Form.Item>
          
          <Form.Item
            name="agent_id"
            label="关联智能体"
            rules={[{ required: true, message: '请选择关联智能体' }]}
          >
            <Select placeholder="选择关联智能体">
              {(Array.isArray(agents) ? agents : []).map(agent => (
                <Option key={agent.id} value={agent.id}>
                  {agent.name} ({agent.type})
                </Option>
              ))}
            </Select>
          </Form.Item>
          
          {renderParameterFields()}
          
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                创建
              </Button>
              <Button onClick={() => {
                setModalVisible(false);
                form.resetFields();
                setSelectedTemplate(null);
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

export default WorkflowConfig;