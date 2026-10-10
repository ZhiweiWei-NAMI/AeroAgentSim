"""Independent NumPy adversarial checks. No torch import, downloads, or training."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from copy import deepcopy
import numpy as np
from observer_proto.contracts import (Association, FieldSpec, build_history_graph,
    NODE_OBSERVATION, EDGE_ASSOCIATION, EDGE_PHYSICAL)
from observer_proto.fixtures import tiny_history, tiny_queries
from observer_proto.reference import (NumpyPrototype, JointLaw, Rollout,
    query_truth, geometry_edges, known_cdf_targets, hybrid_loss,
    mix_probabilities, softmax, rule_mc)


def epoch_history():
    h = tiny_history()
    offset = 1_800_000_000.
    h.cutoff += offset
    for name in ('times', 'available_time', 'support_end'):
        setattr(h, name, getattr(h, name) + offset)
    for o in h.observations:
        o.event_time += offset; o.available_time += offset; o.support_end += offset
    for a in h.associations:
        a.available_time += offset; a.support_end += offset
    return h.validate()


class IndependentContractTests(unittest.TestCase):
    def test_unix_cutoff_boundary(self):
        h = epoch_history()
        h.times += 2.
        with self.assertRaises(ValueError): h.validate()

    def test_unix_observation_tick_boundary(self):
        h = epoch_history()
        h.observations[0].event_time -= 2.
        with self.assertRaises(ValueError): h.validate()

    def test_future_evidence_rejected(self):
        for name in ('available_time', 'support_end'):
            h = tiny_history(); getattr(h,name)[-1,0,0] = .5
            with self.assertRaises(ValueError): h.validate()
        h=tiny_history(); h.observations[0].available_time=.5
        with self.assertRaises(ValueError): h.validate()
        h=tiny_history(); h.associations[0].support_end=.5
        with self.assertRaises(ValueError): h.validate()

    def test_schema_fails_closed_before_wrong_geometry(self):
        h=tiny_history(); h.fields=(FieldSpec('y'),FieldSpec('x'))
        with self.assertRaises(NotImplementedError): build_history_graph(h)

    def test_observation_stored_once_and_multi_entity_refs(self):
        h=tiny_history(); before=build_history_graph(h)
        h.associations += (Association('obs_0','entity_b',(0,),'fixture:shared-roi'),)
        after=build_history_graph(h)
        self.assertEqual(len(after.features),len(before.features))
        self.assertEqual(int((after.node_types==NODE_OBSERVATION).sum()),6)
        self.assertEqual(int((after.edge_types==EDGE_ASSOCIATION).sum()),4)
        obs_node=[i for i,x in enumerate(after.metadata['nodes']) if x[:3]==('observation','obs_0',0)][0]
        self.assertEqual(int((after.edge_types[:,obs_node]==EDGE_ASSOCIATION).sum()),2)

    def test_overlapping_association_edges_not_order_dependent(self):
        h=tiny_history()
        h.associations += (Association('obs_0','entity_a',(0,),'fixture:second-overlap',score=.2),)
        try:
            forward=build_history_graph(h)
        except ValueError:
            return # Explicit fail-closed duplicate policy is a valid bounded choice.
        reverse=deepcopy(h);reverse.associations=tuple(reversed(reverse.associations))
        np.testing.assert_array_equal(forward.edge_weights,build_history_graph(reverse).edge_weights)

    def test_graph_reindex_and_queries_stay_bound(self):
        h=tiny_history(); order=np.array([2,0,1]); hp=h.permute(order)
        g,gp=build_history_graph(h),build_history_graph(hp)
        node_order=np.r_[np.array([3*t+order for t in range(21)]).ravel(),np.arange(63,len(g.features))]
        np.testing.assert_allclose(gp.features,g.features[node_order])
        np.testing.assert_array_equal(gp.edge_types,g.edge_types[np.ix_(node_order,node_order)])
        np.testing.assert_array_equal(gp.edge_weights,g.edge_weights[np.ix_(node_order,node_order)])
        q=tiny_queries()
        for x,y in zip(query_truth(h,q,h.values,h.valid),query_truth(hp,q,hp.values,hp.valid)):
            np.testing.assert_array_equal(x,y)

    def test_unknown_numeric_storage_does_not_become_evidence(self):
        h=tiny_history(); hm=deepcopy(h)
        for name in ('values','uncertainty','available_time','support_end'):
            getattr(hm,name)[~hm.valid]=1e20
        model=NumpyPrototype(h)
        np.testing.assert_array_equal(build_history_graph(h).features,build_history_graph(hm).features)
        np.testing.assert_array_equal(model.encode(h),model.encode(hm))

    def test_complete_and_censored_first_flip_semantics(self):
        initial=np.array([False, True, False, False, False])
        future=np.zeros((5,10),bool)
        future[0,2]=True # flips and returns next observed tick
        future[1]=True; future[1,4:]=False # currently true, flips false
        future[3,4:]=True # changed after unknown interval: partial CDF known
        valid=np.ones_like(future); valid[2,4:]=False; valid[3,2:4]=False
        init_valid=np.ones(5,bool);init_valid[4]=False
        targets,known,bins=known_cdf_targets(initial,init_valid,future,valid)
        np.testing.assert_array_equal(bins,[2,4,-1,-1,-1])
        np.testing.assert_array_equal(known[2],[1,1,1,1,0,0,0,0,0,0])
        np.testing.assert_array_equal(known[3],[1,1,0,0,1,1,1,1,1,1])
        self.assertFalse(known[4].any())
        self.assertTrue(targets[0,2:].all())

    def test_joint_correlated_sample_feedback_and_edges(self):
        h=tiny_history();r=NumpyPrototype(h).rollout(h)
        self.assertEqual(r.states.shape,(8,10,3,2))
        np.testing.assert_array_equal(r.previous_states[:,1:],r.states[:,:-1])
        np.testing.assert_array_equal(r.edges,geometry_edges(r.states,r.valid))
        self.assertGreater(np.abs(r.states[0]-r.states[1]).max(),1e-8)
        cov=r.laws[0].covariance()
        self.assertGreater(np.abs(cov-np.diag(np.diag(cov))).max(),1e-5)
        self.assertGreater(np.linalg.eigvalsh(cov).min(),0.)
        np.testing.assert_array_equal(r.times,h.cutoff+.5*np.arange(1,11))

    def test_joint_missing_subvector_nll(self):
        mu=np.zeros((1,2,2));std=np.full_like(mu,.3);factor=np.arange(4).reshape(1,2,2,1)*.1
        law=JointLaw(mu,std,factor); y=np.array([[[1.,np.nan],[np.nan,-.2]]]);m=np.isfinite(y)
        cov=law.covariance()[np.ix_(m.ravel(),m.ravel())];delta=y[m]
        expected=.5*(2*np.log(2*np.pi)+np.linalg.slogdet(cov)[1]+delta@np.linalg.solve(cov,delta))
        self.assertAlmostEqual(law.masked_nll(y,m),expected)

    def test_same_query_all_paths_and_unknown_not_bucket11(self):
        h=tiny_history();q=tiny_queries();r=NumpyPrototype(h).rollout(h)
        ref=rule_mc(h,q,r)
        manual=[]
        for branch in range(8):
            single=Rollout(r.states[branch:branch+1],r.valid[branch:branch+1],r.edges[branch:branch+1],r.times,[],r.previous_states[branch:branch+1])
            manual.append(rule_mc(h,q,single)['per_path_probs'][0])
        np.testing.assert_array_equal(ref['probabilities'],np.mean(manual,axis=0))
        r.valid[0,0,0]=False
        bad=rule_mc(h,q,r)
        self.assertEqual(bad['bins'][0,0],-1)
        self.assertFalse(bad['valid'][0]); self.assertTrue(np.isnan(bad['probabilities'][0]).all())

    def test_mixture_probability_before_loss_once(self):
        logits=np.zeros((8,2,11)); logits[0,0,0]=9;logits[1:,0,10]=1
        each,p=mix_probabilities(logits)
        np.testing.assert_array_equal(p,softmax(logits).mean(0))
        self.assertGreater(np.abs(p-softmax(logits.mean(0))).max(),.01)
        target=np.zeros((2,10));target[0,2:]=1
        known=np.ones((2,10),bool);known[1,4:]=False
        cdf=p[:,:10].cumsum(-1)
        mse=((cdf[:,:9]-target[:,:9])**2)[known[:,:9]].mean()
        final=cdf[:,9];bce=(-(target[:,9]*np.log(final)+(1-target[:,9])*np.log(1-final)))[known[:,9]].mean()
        result=hybrid_loss(p,target,known)
        self.assertAlmostEqual(result['loss'],mse+.5*bce)
        self.assertEqual(result['timing_count'],13);self.assertEqual(result['terminal_count'],1)
        poisoned=target.copy();poisoned[~known]=np.nan
        self.assertAlmostEqual(hybrid_loss(p,poisoned,known)['loss'],result['loss'])
        self.assertEqual(hybrid_loss(p,poisoned,np.zeros_like(known))['loss'],0)

    def test_stage_parameter_arrays_untied(self):
        model=NumpyPrototype(tiny_history())
        a=[v for v in vars(model.stage1_encoder).values() if isinstance(v,np.ndarray)]
        b=[v for v in vars(model.stage2_encoder).values() if isinstance(v,np.ndarray)]
        self.assertFalse(any(np.shares_memory(x,y) for x in a for y in b))

if __name__=='__main__':unittest.main(verbosity=2)
