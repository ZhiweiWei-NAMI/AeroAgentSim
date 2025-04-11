import React, { useState, useEffect } from 'react';
import { Route, Routes, Link, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography, Breadcrumb } from 'antd';
import {
  HomeOutlined,
  RobotOutlined,
  HistoryOutlined,
  SettingOutlined,
  PlayCircleOutlined,
  AimOutlined,
  DashboardOutlined,
  TeamOutlined,
  GlobalOutlined
} from '@ant-design/icons';

// 导入页面组件
import Dashboard from './pages/Dashboard';
import DroneMonitor from './pages/DroneMonitor';
import DroneHistory from './pages/DroneHistory';
import WorkflowConfig from './pages/WorkflowConfig';
import AgentConfig from './pages/AgentConfig';
import SimulationControl from './pages/SimulationControl';
import Simulation3DMapView from './pages/Simulation3DMapView';

// 导入样式
import './App.css';

const { Header, Content, Footer, Sider } = Layout;
const { Title } = Typography;

function App() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [currentPath, setCurrentPath] = useState(location.pathname);
  
  // 当路由变化时更新currentPath
  useEffect(() => {
    setCurrentPath(location.pathname);
  }, [location]);

  // 面包屑映射
  const breadcrumbNameMap = {
    '/': '仪表盘',
    '/drones': '无人机监控',
    '/history': '历史数据',
    '/workflows': '工作流配置',
    '/agents': '智能体配置',
    '/simulation': '仿真控制',
    '/3dmap': '3D地图视图',
  };
  
  // 动态路径的面包屑映射
  const getDynamicBreadcrumbName = (path) => {
    // 匹配 /drones/:droneId/history 格式的路径
    const droneHistoryMatch = path.match(/^\/drones\/(.+)\/history$/);
    if (droneHistoryMatch) {
      return `无人机 ${droneHistoryMatch[1]} 历史数据`;
    }
    return null;
  };

  // 获取当前面包屑路径
  const getBreadcrumb = (path) => {
    // 首先检查是否是动态路径
    const droneHistoryMatch = path.match(/^\/drones\/(.+)\/history$/);
    
    // 如果是无人机历史数据路径，使用特殊处理
    if (droneHistoryMatch) {
      const droneId = droneHistoryMatch[1];
      return [
        <Breadcrumb.Item key="home">
          <Link to="/"><HomeOutlined /> 首页</Link>
        </Breadcrumb.Item>,
        <Breadcrumb.Item key="/drones">
          <Link to="/drones">无人机监控</Link>
        </Breadcrumb.Item>,
        <Breadcrumb.Item key={path}>
          无人机 {droneId} 历史数据
        </Breadcrumb.Item>
      ];
    }
    
    // 其他路径使用常规处理
    const pathSnippets = path.split('/').filter(i => i);
    const extraBreadcrumbItems = pathSnippets.map((_, index) => {
      const url = `/${pathSnippets.slice(0, index + 1).join('/')}`;
      return (
        <Breadcrumb.Item key={url}>
          <Link to={url}>{breadcrumbNameMap[url] || url}</Link>
        </Breadcrumb.Item>
      );
    });
    
    const breadcrumbItems = [
      <Breadcrumb.Item key="home">
        <Link to="/"><HomeOutlined /> 首页</Link>
      </Breadcrumb.Item>,
    ].concat(extraBreadcrumbItems);
    
    return breadcrumbItems;
  };

  // 处理菜单点击
  const handleMenuClick = (e) => {
    setCurrentPath(e.key);
  };

  return (
      <Layout style={{ minHeight: '100vh' }}>
        <Sider 
          collapsible 
          collapsed={collapsed} 
          onCollapse={value => setCollapsed(value)}
          theme="dark"
        >
          <div className="logo">
            {!collapsed && <Title level={5} style={{ color: 'white', margin: '0' }}>AirFogSim</Title>}
            {collapsed && <Title level={5} style={{ color: 'white', margin: '0', fontSize: '14px' }}>AFS</Title>}
          </div>
          
          <Menu 
            theme="dark" 
            selectedKeys={[currentPath]} 
            mode="inline"
            onClick={handleMenuClick}
          >
            <Menu.Item key="/" icon={<DashboardOutlined />}>
              <Link to="/">仪表盘</Link>
            </Menu.Item>
            
            <Menu.Item key="/drones" icon={<RobotOutlined />}>
              <Link to="/drones">无人机监控</Link>
            </Menu.Item>
            
            {/* <Menu.Item key="/history" icon={<HistoryOutlined />}>
              <Link to="/history">历史数据</Link>
            </Menu.Item> */}
            
            <Menu.Item key="/workflows" icon={<AimOutlined />}>
              <Link to="/workflows">工作流配置</Link>
            </Menu.Item>
            
            <Menu.Item key="/agents" icon={<TeamOutlined />}>
              <Link to="/agents">智能体配置</Link>
            </Menu.Item>
            
            <Menu.Item key="/simulation" icon={<PlayCircleOutlined />}>
              <Link to="/simulation">仿真控制</Link>
            </Menu.Item>
            
            <Menu.Item key="/3dmap" icon={<GlobalOutlined />}>
              <Link to="/3dmap">3D地图视图</Link>
            </Menu.Item>
            
            <Menu.Item key="/settings" icon={<SettingOutlined />}>
              <Link to="/settings">系统设置</Link>
            </Menu.Item>
          </Menu>
        </Sider>
        
        <Layout className="site-layout">
          <Header className="site-layout-background" style={{ padding: 0, background: '#fff' }}>
            <div style={{ display: 'flex', alignItems: 'center', height: '100%', paddingLeft: '24px' }}>
              <Title level={3} style={{ margin: 0 }}>无人机仿真可视化系统</Title>
            </div>
          </Header>
          
          <Content style={{ margin: '0 16px' }}>
            <Breadcrumb style={{ margin: '16px 0' }}>
              {getBreadcrumb(currentPath)}
            </Breadcrumb>
            
            <div className="site-layout-background" style={{ padding: 24, minHeight: 360 }}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/drones" element={<DroneMonitor />} />
                <Route path="/history" element={<DroneHistory />} />
                <Route path="/drones/:droneId/history" element={<DroneHistory />} />
                <Route path="/workflows" element={<WorkflowConfig />} />
                <Route path="/agents" element={<AgentConfig />} />
                <Route path="/simulation" element={<SimulationControl />} />
                <Route path="/3dmap" element={<Simulation3DMapView />} />
                <Route path="/settings" element={<div>系统设置页面</div>} />
              </Routes>
            </div>
          </Content>
          
          <Footer style={{ textAlign: 'center' }}>
            AirFogSim 无人机仿真可视化系统 ©{new Date().getFullYear()} 版权所有
          </Footer>
        </Layout>
      </Layout>
  );
}

export default App;