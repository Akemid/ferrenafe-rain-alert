import { App } from 'aws-cdk-lib/core';
import { Template } from 'aws-cdk-lib/assertions';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';

/**
 * Guards the one property that makes a public bucket safe here: the
 * `s3:GetObject` grant is scoped to the single `status.json` key, never to
 * the bucket itself or to `/*`. Synthesizes in-process via
 * `Template.fromStack` — no credentials, no network, no deploy.
 */
function synthesizeTemplate(): Template {
  const app = new App();
  const stack = new PublicSnapshotStack(app, 'TestStack');
  return Template.fromStack(stack);
}

describe('PublicSnapshotStack bucket policy', () => {
  test('the only public statement grants s3:GetObject on the bucket ARN joined with /status.json, not on the bucket or /*', () => {
    const template = synthesizeTemplate();

    const buckets = template.findResources('AWS::S3::Bucket');
    const bucketLogicalIds = Object.keys(buckets);
    expect(bucketLogicalIds).toHaveLength(1);
    const [bucketLogicalId] = bucketLogicalIds;

    const policies = template.findResources('AWS::S3::BucketPolicy');
    const policyLogicalIds = Object.keys(policies);
    expect(policyLogicalIds).toHaveLength(1);

    const statements = policies[policyLogicalIds[0]].Properties.PolicyDocument
      .Statement as Array<Record<string, unknown>>;

    // The auto-delete custom resource's role also gets a statement on this
    // policy (DeleteObject*/GetBucket*/List*/PutBucketPolicy); filter down
    // to the one statement whose principal is the public ("*"), which is
    // the entire security argument for this bucket.
    const publicStatements = statements.filter(
      (statement) => JSON.stringify(statement.Principal) === JSON.stringify({ AWS: '*' }),
    );
    expect(publicStatements).toHaveLength(1);
    const [publicStatement] = publicStatements;

    expect(publicStatement.Action).toBe('s3:GetObject');

    // Written to fail if the resource were the bucket ARN alone (no Join)
    // or ended in "/*" instead of "/status.json".
    expect(publicStatement.Resource).toEqual({
      'Fn::Join': ['', [{ 'Fn::GetAtt': [bucketLogicalId, 'Arn'] }, '/status.json']],
    });
  });

  test('PublicAccessBlockConfiguration blocks ACLs but allows this bucket policy to grant public read', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        IgnorePublicAcls: true,
        BlockPublicPolicy: false,
        RestrictPublicBuckets: false,
      },
    });
  });
});
