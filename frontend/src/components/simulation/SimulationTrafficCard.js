import React, { useState, useEffect } from 'react';
import { Card, Form, Select, Input, Radio, Button, message, Upload, Spin } from 'antd';
import { UploadOutlined, SyncOutlined, ReloadOutlined } from '@ant-design/icons';
import { useSimulation } from '../../contexts/SimulationContext';
import axios from 'axios';

// 交通流仿真配置卡片组件
const SimulationTrafficCard = () => {
  const { envParams, handleEnvParamChange } = useSimulation();
  const [fileList, setFileList] = useState([]);
  const [sumoFileList, setSumoFileList] = useState([]);
  const [csvFiles, setCsvFiles] = useState([]);
  const [sumoConfigs, setSumoConfigs] = useState([]);
  const [loading, setLoading] = useState(false);
  
  // 初始化时检查是否已有交通相关参数
  useEffect(() => {
    if (!envParams.trafficSource) {
      // 设置默认值
      handleEnvParamChange('trafficSource', 'none');
    }
    // 加载可用的CSV文件和SUMO配置文件
    fetchTrafficFiles();
  }, []);
  
  // 获取可用的交通文件
  const fetchTrafficFiles = async () => {
    try {
      // 获取CSV文件
      const csvResponse = await axios.get('/api/traffic/csv_files');
      setCsvFiles(csvResponse.data || []);
      
      // 获取SUMO配置文件
      const sumoResponse = await axios.get('/api/traffic/sumo_configs');
      setSumoConfigs(sumoResponse.data || []);
    } catch (error) {
      console.error('获取交通文件失败:', error);
      message.error('获取交通文件列表失败');
    }
  };

  // 处理交通源类型变更
  const handleTrafficSourceChange = (value) => {
    handleEnvParamChange('trafficSource', value);
  };

  // 处理CSV文件上传
  const handleCsvUpload = (info) => {
    let fileList = [...info.fileList];
    
    // 限制只能上传一个文件
    fileList = fileList.slice(-1);
    
    setFileList(fileList);
    
    if (info.file.status === 'done') {
      message.success(`${info.file.name} 上传成功`);
      handleEnvParamChange('trafficCsvFile', info.file.response.path || `data/traffic/file/${info.file.name}`);
    } else if (info.file.status === 'error') {
      message.error(`${info.file.name} 上传失败`);
    }
  };

  // 处理SUMO配置文件上传
  const handleSumoConfigUpload = (info) => {
    let fileList = [...info.fileList];
    
    // 限制只能上传一个文件
    fileList = fileList.slice(-1);
    
    setSumoFileList(fileList);
    
    if (info.file.status === 'done') {
      message.success(`${info.file.name} 上传成功`);
      handleEnvParamChange('sumoConfigFile', info.file.response.path || `data/traffic/sumocfg/${info.file.name}`);
    } else if (info.file.status === 'error') {
      message.error(`${info.file.name} 上传失败`);
    }
  };

  // 生成SUMO路网文件
  const handleGenerateSumoNetwork = async () => {
    // 检查参数是否存在且为有效数字
    const lat = parseFloat(envParams.mapCenterLat);
    const lng = parseFloat(envParams.mapCenterLng);
    const radius = parseFloat(envParams.mapRadius);
    console.log(lat,lng,radius);
    if (isNaN(lat) || isNaN(lng) || isNaN(radius) || radius <= 0) {
      message.error('请确保OSM地图中心点和半径已设置且为有效的正数');
      return;
    }
    
    try {
      setLoading(true);
      message.loading({ content: '正在生成SUMO路网文件，这可能需要几分钟...', key: 'sumoGeneration', duration: 0 });
      
      // 调用后端API生成SUMO路网文件
      const response = await axios.post('/api/traffic/generate_sumo_network', {
        center_lat: lat,
        center_lng: lng,
        radius_km: radius
      });
      
      if (response.data && response.data.status === 'success') {
        message.success({ content: 'SUMO路网文件生成成功', key: 'sumoGeneration' });
        handleEnvParamChange('sumoNetworkGenerated', true);
        
        // 设置生成的SUMO配置文件路径
        if (response.data.files && response.data.files.sumocfg_file) {
          handleEnvParamChange('sumoConfigFile', response.data.files.sumocfg_file);
          
          // 刷新SUMO配置文件列表
          fetchTrafficFiles();
        }
      } else {
        message.error({ content: '生成SUMO路网文件失败', key: 'sumoGeneration' });
      }
    } catch (error) {
      console.error('生成SUMO路网文件失败:', error);
      message.error({ content: `生成SUMO路网文件失败: ${error.message}`, key: 'sumoGeneration' });
    } finally {
      setLoading(false);
    }
  };
  
  // 刷新文件列表
  const handleRefreshFiles = () => {
    fetchTrafficFiles();
    message.info('已刷新文件列表');
  };

  return (
    <Card type="inner" title="交通流仿真配置">
      <Spin spinning={loading}>
        <Form layout="vertical">
          <Form.Item label="交通数据源">
            <Radio.Group
              value={envParams.trafficSource || 'none'}
              onChange={(e) => handleTrafficSourceChange(e.target.value)}
            >
              <Radio.Button value="none">不使用</Radio.Button>
              <Radio.Button value="csv">CSV文件</Radio.Button>
              <Radio.Button value="sumo">SUMO仿真器</Radio.Button>
            </Radio.Group>
          </Form.Item>

          {envParams.trafficSource === 'csv' && (
            <Form.Item label="交通流CSV文件">
              <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center' }}>
                <Select
                  style={{ width: '80%', marginRight: 8 }}
                  placeholder="选择CSV文件"
                  value={envParams.trafficCsvFile}
                  onChange={(value) => handleEnvParamChange('trafficCsvFile', value)}
                >
                  {csvFiles.map(file => (
                    <Select.Option key={file.path} value={file.path}>
                      {file.name}
                    </Select.Option>
                  ))}
                </Select>
                <Button
                  icon={<ReloadOutlined />}
                  onClick={handleRefreshFiles}
                  title="刷新文件列表"
                />
              </div>
              
              <Upload
                action="/api/upload"
                fileList={fileList}
                onChange={handleCsvUpload}
                accept=".csv"
              >
                <Button icon={<UploadOutlined />}>上传新文件</Button>
              </Upload>
              <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
                支持CSV格式的交通流数据，包含车辆ID、时间戳、位置等信息
              </div>
            </Form.Item>
          )}

          {envParams.trafficSource === 'sumo' && (
            <>
              <Form.Item label="SUMO配置">
                <div style={{ marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<SyncOutlined />}
                    onClick={handleGenerateSumoNetwork}
                    style={{ marginRight: 8 }}
                    loading={loading}
                  >
                    从OSM生成路网
                  </Button>
                  <span style={{ fontSize: 12, color: '#888' }}>
                    基于当前OSM地图设置生成SUMO路网文件
                  </span>
                </div>
                
                <div style={{ marginBottom: 16 }}>
                  <Form.Item label="选择SUMO配置文件">
                    <div style={{ display: 'flex', alignItems: 'center' }}>
                      <Select
                        style={{ width: '80%', marginRight: 8 }}
                        placeholder="选择SUMO配置文件"
                        value={envParams.sumoConfigFile}
                        onChange={(value) => handleEnvParamChange('sumoConfigFile', value)}
                      >
                        {sumoConfigs.map(file => (
                          <Select.Option key={file.path} value={file.path}>
                            {file.name}
                          </Select.Option>
                        ))}
                      </Select>
                      <Button
                        icon={<ReloadOutlined />}
                        onClick={handleRefreshFiles}
                        title="刷新文件列表"
                      />
                    </div>
                  </Form.Item>
                  
                  <Upload
                    action="/api/upload"
                    fileList={sumoFileList}
                    onChange={handleSumoConfigUpload}
                    accept=".sumocfg"
                  >
                    <Button icon={<UploadOutlined />}>上传新文件</Button>
                  </Upload>
                </div>
              </Form.Item>
            
            <Form.Item label="SUMO端口">
              <Input
                type="number"
                value={envParams.sumoPort || 8813}
                onChange={(e) => handleEnvParamChange('sumoPort', parseInt(e.target.value))}
                min={1000}
                max={65535}
                style={{ width: 150 }}
              />
            </Form.Item>
            
            <Form.Item label="使用GUI版本">
              <Radio.Group
                value={envParams.sumoGui || false}
                onChange={(e) => handleEnvParamChange('sumoGui', e.target.value)}
              >
                <Radio value={true}>是</Radio>
                <Radio value={false}>否</Radio>
              </Radio.Group>
            </Form.Item>
          </>
        )}
        </Form>
      </Spin>
    </Card>
  );
};

export default SimulationTrafficCard;