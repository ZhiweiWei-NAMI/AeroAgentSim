//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Lesser General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
// 
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Lesser General Public License for more details.
// 
// You should have received a copy of the GNU Lesser General Public License
// along with this program.  If not, see http://www.gnu.org/licenses/.
// 

package fognetsim.simulations.testing;
import fognetsim.node.compute.AccessPointCompute;
import fognetsim.node.compute.StandardCompute;
import fognetsim.node.compute.WirelessCompute;
import inet.node.inet.Router;
import ned.DatarateChannel;
import inet.networklayer.configurator.ipv4.IPv4NetworkConfigurator;
import inet.physicallayer.ieee80211.packetlevel.Ieee80211ScalarRadioMedium;

//
// TODO documentation
//
network WirelessNetwork
{
    parameters:

        @display("bgb=580,510");
    types:
        channel C extends DatarateChannel
        {
            datarate = 100Mbps;
            delay = 0.1us;
        }

    submodules:
        ComputeBroker1: StandardCompute {
            @display("p=123,465;i=device/router");
        }
        ComputeBroker2: StandardCompute {
            @display("p=467,465;i=device/router");
        }
        BaseBroker: StandardCompute {
            @display("p=293,305;i=device/server;q=Tasks");
        }
        router: Router {
            @display("p=293,216;q=Tasks");
        }
        ap2: AccessPointCompute {
            @display("p=467,175;q=queue");
        }
        ap1: AccessPointCompute {
            @display("p=123,175;q=queue");
        }
        user: WirelessCompute {
            @display("p=397,78");
        }
        router1: Router {
            @display("p=293,401;q=Tasks");
        }
        configurator: IPv4NetworkConfigurator {
            //config = xml("<config><interface user='*' address='145.236.16.2' netmask='255.255.255.0'/></config>");
            @display("p=140,50");
        }
        radioMedium: Ieee80211ScalarRadioMedium {
            @display("p=60,50");
        }

    connections:
        ap2.ethg++ <--> C <--> ap1.ethg++;
        router.ethg++ <--> C <--> ap1.ethg++;
        router.ethg++ <--> C <--> ap2.ethg++;
        router.ethg++ <--> C <--> BaseBroker.ethg++;
        router1.ethg++ <--> C <--> BaseBroker.ethg++;
        router1.ethg++ <--> C <--> ComputeBroker1.ethg++;
        router1.ethg++ <--> C <--> ComputeBroker2.ethg++;
}

