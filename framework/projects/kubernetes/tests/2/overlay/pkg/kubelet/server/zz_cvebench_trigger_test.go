package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	utilfeature "k8s.io/apiserver/pkg/util/feature"
	"k8s.io/client-go/tools/record"
	featuregatetesting "k8s.io/component-base/featuregate/testing"
	"k8s.io/kubernetes/pkg/features"
	"k8s.io/kubernetes/pkg/kubelet/server/stats"
)

// The read-only server is built the way ListenAndServeKubeletReadOnlyServer builds
// it: no authenticator and no kubelet configuration.
func TestReadOnlyServerDoesNotServeCheckpoint(t *testing.T) {
	featuregatetesting.SetFeatureGateDuringTest(t, utilfeature.DefaultFeatureGate, features.ContainerCheckpoint, true)

	fw := newServerTest()
	defer fw.testHTTPServer.Close()
	setPodByNameFunc(fw, "ns", "pod", "ctr")

	ro := NewServer(fw.fakeKubelet, stats.NewResourceAnalyzer(fw.fakeKubelet, time.Minute, &record.FakeRecorder{}), nil, nil, nil)
	srv := httptest.NewServer(&ro)
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/checkpoint/ns/pod/ctr", "", nil)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("POST /checkpoint/ns/pod/ctr on the read-only server: status %d, want %d", resp.StatusCode, http.StatusNotFound)
	}
}
