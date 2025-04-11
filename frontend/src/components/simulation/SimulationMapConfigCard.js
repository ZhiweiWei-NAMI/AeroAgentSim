import React from 'react';
import { Card, Form, Row, Col, InputNumber, Switch } from 'antd';
import { useSimulation } from '../../contexts/SimulationContext';

// 地图配置卡片组件
const SimulationMapConfigCard = () => {
  const { envParams, handleEnvParamChange } = useSimulation();

  return (
    <Card type="inner" title="OpenStreetMap 地图配置">
      <Form layout="vertical">
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item label="地图中心点">
              <Row gutter={8}>
                <Col span={12}>
                  <InputNumber
                    addonBefore="纬度"
                    style={{ width: '100%' }}
                    value={envParams.mapCenterLat}
                    onChange={(value) => handleEnvParamChange('mapCenterLat', value)}
                    step={0.0001}
                  />
                </Col>
                <Col span={12}>
                  <InputNumber
                    addonBefore="经度"
                    style={{ width: '100%' }}
                    value={envParams.mapCenterLng}
                    onChange={(value) => handleEnvParamChange('mapCenterLng', value)}
                    step={0.0001}
                  />
                </Col>
              </Row>
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item label="半径(公里)">
              <InputNumber
                style={{ width: '100%' }}
                value={envParams.mapRadius}
                onChange={(value) => handleEnvParamChange('mapRadius', value)}
                min={0.1}
                max={10}
                step={0.1}
              />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item label="加载OSM建筑物">
              <Switch
                checked={envParams.loadOsmBuildings}
                onChange={(checked) => handleEnvParamChange('loadOsmBuildings', checked)}
              />
            </Form.Item>
          </Col>
        </Row>
        <div style={{ color: '#888', fontSize: '12px', marginTop: '-10px' }}>
          注意: 加载大范围的OSM数据可能会影响性能。建议半径不超过3公里。
        </div>
        
        <div style={{ color: '#1890ff', fontSize: '12px', marginTop: '10px', padding: '8px', backgroundColor: '#e6f7ff', borderRadius: '4px' }}>
          提示: 设置OSM地图后，可以在"交通流仿真配置"部分生成对应的SUMO路网文件。
        </div>
      </Form>
    </Card>
  );
};

export default SimulationMapConfigCard;