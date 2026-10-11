package main

import (
	"io"
	"net"
	"testing"
	"time"
)

func TestRelayPreservesBytesAndHalfClose(t *testing.T) {
	backend, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer backend.Close()
	go func() {
		connection, err := backend.Accept()
		if err != nil {
			return
		}
		defer connection.Close()
		body, err := io.ReadAll(connection)
		if err == nil {
			connection.Write(append([]byte("reply:"), body...))
		}
	}()
	ingress, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ingress.Close()
	permits := make(chan struct{}, 1)
	finished := make(chan struct{})
	go func() {
		connection, err := ingress.Accept()
		if err != nil {
			return
		}
		permits <- struct{}{}
		relay(connection, backend.Addr().String(), permits)
		close(finished)
	}()
	client, err := net.DialTimeout("tcp4", ingress.Addr().String(), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	client.SetDeadline(time.Now().Add(5 * time.Second))
	client.Write([]byte("candidate bytes"))
	client.(*net.TCPConn).CloseWrite()
	body, err := io.ReadAll(client)
	if err != nil || string(body) != "reply:candidate bytes" {
		t.Fatalf("bytes=%q error=%v", body, err)
	}
	select {
	case <-finished:
	case <-time.After(5 * time.Second):
		t.Fatal("relay retained a closed connection")
	}
	if len(permits) != 0 {
		t.Fatal("connection permit was retained")
	}
}
